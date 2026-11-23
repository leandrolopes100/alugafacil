from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from apps.core.mixins import PermissaoRequeridaMixin
from apps.core.permissoes import tem_permissao
from django.db.models import Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView, View

from .forms import (ReservaForm, ContratoForm, CheckoutForm, CheckinForm,
                    EncerramentoAntecipadoForm, AdicionalContratoForm,
                    AvariaContratoForm, AjusteAvariaForm, PagamentoContratoForm)
from .models import (AdicionalContrato, AvariaContrato, Contrato, FotoContrato,
                     HistoricoContrato, PagamentoContrato, ParcelaContrato,
                     Reserva, gerar_parcelas, proximo_vencimento_contratual)
from apps.fleet.models import Veiculo
from apps.financeiro.models import NotificacaoCobranca


def _brl(v):
    """Formata Decimal/float como BRL para uso em strings Python (não em templates)."""
    return 'R$ ' + f'{float(v or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _checklist_saida(contrato):
    """Retorna lista de (ok: bool|None, mensagem: str) com o checklist de saída
    do veículo — usado tanto no check-out normal quanto na criação retroativa."""
    hoje = timezone.localdate()
    itens = []

    # 1. Cliente bloqueado
    if contrato.cliente.bloqueado:
        itens.append((False, f'Cliente bloqueado: {contrato.cliente.motivo_bloqueio}'))
    else:
        itens.append((True, 'Cliente ativo'))

    # 2. CNH válida (usa prefetch cache — sem query adicional)
    cnh = next((c for c in contrato.cliente.cnhs.all() if c.principal), None)
    if not cnh:
        itens.append((False, 'Cliente sem CNH cadastrada'))
    elif cnh.vencida:
        itens.append((False, f'CNH vencida em {cnh.validade.strftime("%d/%m/%Y")}'))
    else:
        itens.append((True, f'CNH válida até {cnh.validade.strftime("%d/%m/%Y")}'))

    # 3. CRLV do veículo (usa prefetch cache — sem query adicional)
    docs = list(contrato.veiculo.documentos.all())
    crlv = next((d for d in docs if d.tipo == 'crlv'), None)
    if crlv and crlv.vencido:
        itens.append((False, f'CRLV vencido em {crlv.data_validade.strftime("%d/%m/%Y")}'))
    elif crlv:
        itens.append((True, f'CRLV em dia até {crlv.data_validade.strftime("%d/%m/%Y")}'))
    else:
        itens.append((None, 'CRLV não cadastrado — verifique a documentação do veículo'))

    # 4. Seguro do veículo (alerta, não bloqueia — documentação opcional)
    seguro = next((d for d in docs if d.tipo == 'seguro'), None)
    if seguro and seguro.vencido:
        itens.append((None, f'Seguro vencido em {seguro.data_validade.strftime("%d/%m/%Y")} — regularize assim que possível'))
    elif seguro:
        itens.append((True, f'Seguro em dia até {seguro.data_validade.strftime("%d/%m/%Y")}'))
    else:
        itens.append((None, 'Seguro não cadastrado — verifique a documentação do veículo'))

    # 5. Caução
    if contrato.caucao_valor > 0:
        if contrato.caucao_situacao == 'pendente':
            itens.append((False, f'Caução de {_brl(contrato.caucao_valor)} não registrado — informe a situação abaixo'))
        else:
            itens.append((True, f'Caução de {_brl(contrato.caucao_valor)} — {contrato.get_caucao_situacao_display()}'))
    else:
        itens.append((True, 'Sem caução exigido'))

    # 6. Primeira semana — aviso, não bloqueia; parcela pendente gerada ao confirmar checkout
    from .models import ciclos_de_cobranca
    inicio = timezone.localdate(contrato.data_saida or timezone.now())
    primeiro_ciclo = ciclos_de_cobranca(
        inicio, inicio + timedelta(days=max(contrato._dias_previstos(), 1)), contrato.forma_cobranca,
    )[0]
    valor_primeira_semana = contrato.valor_ciclo(primeiro_ciclo[1], primeiro_ciclo[2])
    total_locacao_pago = PagamentoContrato.objects.filter(
        contrato=contrato, tipo='locacao'
    ).aggregate(s=Sum('valor'))['s'] or Decimal('0')
    if total_locacao_pago < valor_primeira_semana:
        falta = valor_primeira_semana - total_locacao_pago
        itens.append((None, (
            f'Primeira parcela ({_brl(valor_primeira_semana)}) ainda não paga — '
            f'faltam {_brl(falta)}. '
            f'A parcela pendente será criada ao confirmar o check-out.'
        )))
    else:
        itens.append((True, f'Primeira parcela de {_brl(valor_primeira_semana)} registrada'))

    # 7. Multas de trânsito com prazo vencido (1 query, contado em Python)
    from apps.financeiro.models import MultaTransito
    prazos = list(MultaTransito.objects.filter(
        veiculo=contrato.veiculo,
        situacao='pendente_identificacao',
    ).values_list('prazo_indicacao', flat=True))
    multas_vencidas = sum(1 for p in prazos if p is not None and p < hoje)
    multas_criticas = sum(1 for p in prazos if p is not None and p >= hoje)
    if multas_vencidas:
        itens.append((False, f'{multas_vencidas} multa(s) com prazo de indicação de condutor VENCIDO — resolva antes de liberar o veículo'))
    elif multas_criticas:
        itens.append((True, f'Atenção: {multas_criticas} multa(s) com prazo de indicação próximo do vencimento'))
    else:
        itens.append((True, 'Sem multas com prazo de indicação pendente'))

    return itens


def _pode_criar_retroativo(user):
    return tem_permissao(user, 'contratos_administrar')


def _gerar_parcela_acerto_se_necessario(contrato):
    """Cria a parcela de acerto final (km excedente / dias extras / diferença de
    combustível) apurados em calcular_fechamento(), se houver valor a cobrar.
    Usado tanto no check-in normal quanto no encerramento antecipado.
    Retorna o total do acerto (Decimal 0 se nada foi gerado).
    """
    total_acerto = (
        contrato.valor_km_excedente_total
        + contrato.valor_dias_extras
        + contrato.valor_diferenca_combustivel
    )
    if total_acerto <= 0:
        return Decimal('0.00')

    partes_obs = []
    if contrato.valor_km_excedente_total > 0:
        partes_obs.append(
            f'{contrato.km_excedente} km excedente '
            f'× {_brl(contrato.valor_km_excedente)} = {_brl(contrato.valor_km_excedente_total)}'
        )
    if contrato.valor_dias_extras > 0:
        adicionais = contrato.diaria_adicionais
        composicao = (
            f' ({_brl(contrato.diaria)} do veículo + {_brl(adicionais)} de adicionais)' if adicionais else ''
        )
        partes_obs.append(
            f'{contrato.dias_extras} dia(s) extra(s) '
            f'× {_brl(contrato.diaria_com_adicionais)}{composicao} = {_brl(contrato.valor_dias_extras)}'
        )
    if contrato.valor_diferenca_combustivel > 0:
        partes_obs.append(
            f'Diferença combustível: {_brl(contrato.valor_diferenca_combustivel)}'
        )
    proximo_num = (
        contrato.parcelas.aggregate(m=Max('numero'))['m'] or 0
    ) + 1
    ParcelaContrato.objects.create(
        contrato=contrato,
        numero=proximo_num,
        tipo='acerto',
        data_vencimento=timezone.localdate(contrato.data_devolucao_real),
        valor=total_acerto,
        origem='original',
        situacao='pendente',
        observacoes=' | '.join(partes_obs),
    )
    return total_acerto


def _regra_devolucao(contrato, tolerancia=None):
    tolerancia = contrato.tolerancia_devolucao() if tolerancia is None else tolerancia
    saida = timezone.localdate(contrato.data_saida)
    return {
        'saida': timezone.localtime(contrato.data_saida).isoformat(),
        'dias_contratados': contrato.dias_contratados_no_fechamento(),
        'tolerancia_minutos': int(tolerancia.total_seconds() // 60),
        'diaria': float(contrato.diaria_com_adicionais),
        'parcelas_futuras': [
            {
                'numero': p.numero, 'valor': float(p.valor),
                'dia_inicio': ((p.inicio_periodo or p.data_vencimento) - saida).days,
            }
            for p in contrato.parcelas_abertas().filter(tipo__in=['semanal', 'mensal'])
        ],
    }


def _mensagem_caucao(plano):
    if not plano:
        return None
    partes = [f'Caução de {_brl(plano["caucao"])}:']
    if plano['total_abatido']:
        partes.append(f'{_brl(plano["total_abatido"])} abatido de dívidas do contrato;')
    if plano['devolver']:
        partes.append(f'{_brl(plano["devolver"])} devolvido ao cliente.')
    else:
        partes.append('nada a devolver.')
    if plano['restante']:
        partes.append(f'Ainda faltam {_brl(plano["restante"])} a cobrar do cliente.')
    multas = plano['multas_sem_condutor']
    if multas:
        partes.append(
            f'{len(multas)} multa(s) sem condutor identificado ({_brl(sum(m.valor for m in multas))}) '
            'não foram abatidas: identifique o condutor para cobrar.'
        )
    return ' '.join(partes)


# ─── Reservas ────────────────────────────────────────────────────────────────

class ReservaListView(PermissaoRequeridaMixin, ListView):
    permissao = 'contratos_operar'
    model = Reserva
    template_name = 'contracts/reserva_lista.html'
    context_object_name = 'reservas'
    paginate_by = 20

    def get_queryset(self):
        qs = Reserva.objects.select_related('cliente', 'grupo_veiculo')
        situacao = self.request.GET.get('situacao')
        busca = self.request.GET.get('busca', '').strip()
        if situacao:
            qs = qs.filter(situacao=situacao)
        if busca:
            filtro = Q(cliente__nome__icontains=busca)
            if busca.isdigit():
                filtro |= Q(pk=int(busca))
            qs = qs.filter(filtro)
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['situacoes'] = Reserva.SITUACAO
        contexto['filtro_situacao'] = self.request.GET.get('situacao', '')
        return contexto


class ReservaCreateView(PermissaoRequeridaMixin, CreateView):
    permissao = 'contratos_operar'
    model = Reserva
    form_class = ReservaForm
    template_name = 'contracts/reserva_form.html'

    def get_success_url(self):
        return reverse_lazy('contratos:reserva-detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Nova Reserva'
        return contexto

    def form_valid(self, form):
        cliente = form.cleaned_data.get('cliente')
        if cliente and cliente.bloqueado:
            form.add_error('cliente', f'Cliente bloqueado: {cliente.motivo_bloqueio or "sem motivo informado"}.')
            return self.form_invalid(form)
        form.instance.criado_por = self.request.user
        messages.success(self.request, 'Reserva criada com sucesso.')
        return super().form_valid(form)


class ReservaUpdateView(PermissaoRequeridaMixin, UpdateView):
    permissao = 'contratos_operar'
    model = Reserva
    form_class = ReservaForm
    template_name = 'contracts/reserva_form.html'

    def get_queryset(self):
        return Reserva.objects.filter(situacao__in=['pendente', 'confirmada'])

    def get_success_url(self):
        return reverse_lazy('contratos:reserva-detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = f'Editar Reserva #{self.object.pk}'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, 'Reserva atualizada com sucesso.')
        return super().form_valid(form)


class ReservaDeleteView(PermissaoRequeridaMixin, DeleteView):
    permissao = 'contratos_administrar'
    model = Reserva
    template_name = 'contracts/reserva_detalhe.html'
    success_url = reverse_lazy('contratos:reserva-lista')

    def get_queryset(self):
        # Impede exclusão se já gerou contrato; no_show nunca gera contrato mas ficava preso
        return Reserva.objects.filter(situacao__in=['pendente', 'cancelada', 'no_show'], contrato__isnull=True)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['reserva'] = self.object
        return contexto

    def form_valid(self, form):
        messages.success(self.request, f'Reserva #{self.object.pk} excluída.')
        return super().form_valid(form)


class ReservaDetailView(PermissaoRequeridaMixin, DetailView):
    permissao = 'contratos_operar'
    model = Reserva
    template_name = 'contracts/reserva_detalhe.html'
    context_object_name = 'reserva'


class ReservaConfirmarView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'
    def post(self, request, pk):
        reserva = get_object_or_404(Reserva, pk=pk)
        reserva.situacao = 'confirmada'
        reserva.save()
        # Bloqueia o veículo para que não seja alocado em outro contrato
        if reserva.veiculo and reserva.veiculo.situacao == 'disponivel':
            reserva.veiculo.situacao = 'reservado'
            reserva.veiculo.save()
        messages.success(request, 'Reserva confirmada.')
        return redirect('contratos:reserva-detalhe', pk=pk)


class ReservaCancelarView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'
    def post(self, request, pk):
        reserva = get_object_or_404(Reserva, pk=pk)
        situacao_anterior = reserva.situacao
        reserva.situacao = 'cancelada'
        reserva.save()
        # Devolve o veículo a 'disponivel' se esta reserva havia bloqueado-o,
        # desde que nenhuma outra reserva confirmada ainda o necessite.
        if (situacao_anterior == 'confirmada'
                and reserva.veiculo
                and reserva.veiculo.situacao == 'reservado'):
            outras_confirmadas = Reserva.objects.filter(
                veiculo=reserva.veiculo,
                situacao='confirmada',
            ).exclude(pk=reserva.pk).exists()
            if not outras_confirmadas:
                reserva.veiculo.situacao = 'disponivel'
                reserva.veiculo.save()
        messages.success(request, 'Reserva cancelada.')
        return redirect('contratos:reserva-lista')


class ReservaNoShowView(PermissaoRequeridaMixin, View):
    """Marca uma reserva como no_show e libera o veículo se aplicável."""
    permissao = 'contratos_operar'
    def post(self, request, pk):
        reserva = get_object_or_404(Reserva, pk=pk, situacao__in=['pendente', 'confirmada'])
        situacao_anterior = reserva.situacao
        reserva.situacao = 'no_show'
        reserva.save()
        if (situacao_anterior == 'confirmada'
                and reserva.veiculo
                and reserva.veiculo.situacao == 'reservado'):
            outras = Reserva.objects.filter(
                veiculo=reserva.veiculo, situacao='confirmada'
            ).exclude(pk=pk).exists()
            if not outras:
                reserva.veiculo.situacao = 'disponivel'
                reserva.veiculo.save()
        messages.warning(request, f'Reserva #{pk} marcada como No Show. Veículo liberado.')
        return redirect('contratos:reserva-detalhe', pk=pk)


class ReservaConverterView(PermissaoRequeridaMixin, View):
    """Converte uma reserva confirmada em contrato aberto."""
    permissao = 'contratos_operar'
    def post(self, request, pk):
        from django.db import transaction
        reserva = get_object_or_404(Reserva, pk=pk, situacao='confirmada')

        if reserva.cliente.bloqueado:
            messages.error(request, f'Não é possível gerar contrato: cliente {reserva.cliente.nome_exibicao} está bloqueado. {reserva.cliente.motivo_bloqueio}')
            return redirect('contratos:reserva-detalhe', pk=pk)

        if not reserva.veiculo:
            messages.error(request, 'Vincule um veículo específico à reserva antes de gerar o contrato.')
            return redirect('contratos:reserva-detalhe', pk=pk)

        if reserva.veiculo.situacao not in ('disponivel', 'reservado'):
            messages.error(request, f'O veículo {reserva.veiculo} está {reserva.veiculo.get_situacao_display()} e não pode ser alocado.')
            return redirect('contratos:reserva-detalhe', pk=pk)

        from apps.fleet.tarifas import forma_cobranca_sugerida
        grupo_tarifa = reserva.grupo_veiculo
        with transaction.atomic():
            contrato = Contrato.objects.create(
                reserva=reserva,
                cliente=reserva.cliente,
                veiculo=reserva.veiculo,
                forma_cobranca=forma_cobranca_sugerida(grupo_tarifa),
                valor_semanal=None if reserva.diaria_cotada else grupo_tarifa.semanal,
                valor_mensal=None if reserva.diaria_cotada else grupo_tarifa.mensal,
                diaria=reserva.diaria_cotada or reserva.grupo_veiculo.diaria,
                km_franquia_diaria=reserva.grupo_veiculo.km_franquia_diaria,
                valor_km_excedente=reserva.grupo_veiculo.valor_km_excedente,
                caucao_valor=reserva.caucao_cotado or reserva.grupo_veiculo.caucao,
                data_devolucao_prevista=reserva.data_devolucao,
                criado_por=request.user,
            )
            reserva.situacao = 'ativa'
            reserva.save(update_fields=['situacao'])
        messages.success(request, f'Contrato {contrato.numero} criado com sucesso.')
        return redirect('contratos:detalhe', pk=contrato.pk)


# ─── Contratos ───────────────────────────────────────────────────────────────

class ContratoListView(PermissaoRequeridaMixin, ListView):
    permissao = 'contratos_consultar'
    model = Contrato
    template_name = 'contracts/lista.html'
    context_object_name = 'contratos'
    paginate_by = 20

    def get_queryset(self):
        qs = Contrato.objects.select_related('cliente', 'veiculo')
        situacao = self.request.GET.get('situacao')
        busca = self.request.GET.get('busca')
        if situacao:
            qs = qs.filter(situacao=situacao)
        if busca:
            qs = qs.filter(
                Q(numero__icontains=busca) |
                Q(cliente__nome__icontains=busca) |
                Q(veiculo__placa__icontains=busca)
            )
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['situacoes'] = Contrato.SITUACAO
        contexto['filtro_situacao'] = self.request.GET.get('situacao', '')
        contexto['filtro_busca'] = self.request.GET.get('busca', '')
        return contexto


class ContratoCreateView(PermissaoRequeridaMixin, CreateView):
    permissao = 'contratos_operar'
    model = Contrato
    form_class = ContratoForm
    template_name = 'contracts/form.html'

    def get_initial(self):
        inicial = super().get_initial()
        veiculo_pk = self.request.GET.get('veiculo')
        if veiculo_pk:
            inicial['veiculo'] = veiculo_pk
        cliente_pk = self.request.GET.get('cliente')
        if cliente_pk:
            inicial['cliente'] = cliente_pk
        return inicial

    def get_success_url(self):
        return reverse_lazy('contratos:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Novo Contrato'
        qs = Veiculo.objects.select_related('grupo').order_by('marca', 'modelo')
        contexto['veiculos_disponiveis'] = qs.filter(situacao__in=['disponivel', 'reservado'])
        contexto['veiculos_indisponiveis'] = qs.exclude(situacao__in=['disponivel', 'reservado'])
        contexto['pode_criar_retroativo'] = _pode_criar_retroativo(self.request.user)
        return contexto

    def form_valid(self, form):
        cliente = form.cleaned_data.get('cliente')
        if cliente and cliente.bloqueado:
            form.add_error('cliente', f'Cliente bloqueado: {cliente.motivo_bloqueio or "sem motivo informado"}.')
            return self.form_invalid(form)
        form.instance.criado_por = self.request.user

        data_saida = form.cleaned_data.get('data_saida')
        if not data_saida:
            messages.success(self.request, 'Contrato criado com sucesso.')
            return super().form_valid(form)

        if not _pode_criar_retroativo(self.request.user):
            form.add_error(None, 'Você não tem permissão para criar contrato retroativo/migrado.')
            return self.form_invalid(form)

        return self._criar_retroativo(form, data_saida)

    def _criar_retroativo(self, form, data_saida):
        """Migração de contrato pré-existente: o veículo já estava com o cliente
        antes do cadastro no sistema. O contrato nasce ativo com a data real de
        saída; a cobrança futura (parcelas) começa a partir de hoje — o período
        anterior é regularizado por um pagamento único informado pelo operador,
        evitando tanto saldo devedor fictício quanto parcelas "vencidas" fantasmas."""
        from django.db import transaction as db_transaction

        contrato = form.save(commit=False)
        contrato.situacao = 'ativo'
        contrato.origem_retroativa = True
        contrato.data_saida = data_saida
        contrato.km_saida = form.cleaned_data['km_saida']
        contrato.combustivel_saida = form.cleaned_data['combustivel_saida']
        contrato.obs_saida = form.cleaned_data.get('justificativa_retroativa', '')

        km_atual_veiculo = form.cleaned_data.get('km_atual_veiculo')
        valor_ja_recebido = form.cleaned_data.get('valor_ja_recebido') or Decimal('0')
        dias_correntes_pagos = form.cleaned_data.get('dias_correntes_pagos')
        forma_regularizacao = form.cleaned_data.get('forma_pagamento_regularizacao') or 'dinheiro'
        agora = timezone.now()
        hoje = timezone.localdate(agora)

        tipo_cobranca = contrato.forma_cobranca
        data_inicio_cobranca = proximo_vencimento_contratual(
            timezone.localdate(contrato.data_saida), hoje, tipo_cobranca,
        )
        fim_parcial = min(data_inicio_cobranca, contrato.fim_periodo_coberto())
        dias_parciais = max((fim_parcial - hoje).days, 0)
        valor_parcial = contrato.valor_base_ciclo(dias_parciais, False) if dias_parciais else Decimal('0.00')
        if dias_correntes_pagos and dias_parciais and valor_ja_recebido < valor_parcial:
            form.add_error(
                'valor_ja_recebido',
                f'Os {dias_parciais} dia(s) de hoje até {fim_parcial:%d/%m/%Y} custam {_brl(valor_parcial)}; '
                f'o valor já recebido precisa cobrir pelo menos isso.',
            )
            return self.form_invalid(form)
        pago_na_parcial = valor_parcial if dias_correntes_pagos and dias_parciais else Decimal('0.00')
        valor_regularizacao = valor_ja_recebido - pago_na_parcial

        with db_transaction.atomic():
            contrato.save()

            veiculo = contrato.veiculo
            km_referencia = km_atual_veiculo if km_atual_veiculo is not None else contrato.km_saida
            veiculo.km_atual = max(veiculo.km_atual or 0, km_referencia)
            veiculo.situacao = 'em_uso'
            veiculo.save()

            # data_pagamento = hoje (não a data retroativa): DRE, Fluxo de Caixa e
            # Relatório de Frota agregam PagamentoContrato por mês de data_pagamento —
            # usar a data histórica reescreveria silenciosamente a receita de um mês
            # já fechado. O período real fica registrado na observação.
            if valor_regularizacao > 0:
                ultimo_dia = max(hoje - timedelta(days=1), timezone.localdate(contrato.data_saida))
                PagamentoContrato.objects.create(
                    contrato=contrato,
                    tipo='regularizacao',
                    forma_pagamento=forma_regularizacao,
                    valor=valor_regularizacao,
                    data_pagamento=agora,
                    registrado_por=self.request.user,
                    observacoes=(
                        f'Regularização de saldo pré-sistema — período de '
                        f'{contrato.data_saida.strftime("%d/%m/%Y")} a {ultimo_dia.strftime("%d/%m/%Y")}. '
                        f'Justificativa: {contrato.obs_saida}'
                    ),
                )

            if contrato.caucao_valor > 0:
                parcela_caucao = ParcelaContrato.objects.create(
                    contrato=contrato,
                    numero=1,
                    tipo='caucao',
                    data_vencimento=timezone.localdate(contrato.data_saida),
                    valor=contrato.caucao_valor,
                    origem='original',
                )
                if contrato.caucao_situacao == 'pago':
                    parcela_caucao.quitar(
                        contrato.caucao_valor, 'dinheiro', agora, self.request.user,
                        f'Caução coletado no registro retroativo/migração '
                        f'(referente à saída em {contrato.data_saida.strftime("%d/%m/%Y")})',
                    )

            # Parcelas de locação: primeira parcela em aberto é o próximo
            # vencimento do cronograma real do contrato (ancorado em data_saida),
            # não a data de hoje — o período já vencido continua resolvido pelo
            # pagamento de regularização acima, não deve virar parcela retroativa.
            if dias_parciais:
                gerar_parcelas(
                    contrato=contrato, data_inicio=hoje, data_fim=fim_parcial,
                    origem='original', tipo_cobranca=tipo_cobranca,
                )
                if pago_na_parcial:
                    parcela_parcial = contrato.parcelas.get(tipo__in=['semanal', 'mensal'], inicio_periodo=hoje)
                    parcela_parcial.quitar(
                        parcela_parcial.valor, forma_regularizacao, agora, self.request.user,
                        'Paga fora do sistema, junto com a regularização da migração.',
                    )
            gerar_parcelas(
                contrato=contrato,
                data_inicio=data_inicio_cobranca,
                data_fim=contrato.fim_periodo_coberto(),
                origem='original',
                tipo_cobranca=tipo_cobranca,
            )

        for ok, msg in _checklist_saida(contrato):
            if ok is False:
                messages.warning(self.request, msg)
        mensagem = f'Contrato {contrato.numero} criado e ativado (registro retroativo/migração).'
        if dias_parciais:
            mensagem += (
                f' Parcela proporcional de {dias_parciais} dia(s) até {fim_parcial:%d/%m/%Y} '
                f'({_brl(valor_parcial)}) {"registrada como paga" if pago_na_parcial else "vencendo hoje"}.'
            )
        messages.success(self.request, mensagem)

        self.object = contrato
        return redirect(self.get_success_url())


class ContratoDetailView(PermissaoRequeridaMixin, DetailView):
    permissao = 'contratos_consultar'
    model = Contrato
    template_name = 'contracts/detalhe.html'
    context_object_name = 'contrato'

    def get_queryset(self):
        return Contrato.objects.select_related(
            'cliente', 'veiculo__grupo', 'reserva', 'criado_por'
        ).prefetch_related(
            'fotos', 'adicionais', 'avarias', 'pagamentos', 'parcelas',
        )

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        todas_fotos = list(self.object.fotos.all())
        contexto['fotos_saida'] = [f for f in todas_fotos if f.momento == 'saida']
        contexto['fotos_devolucao'] = [f for f in todas_fotos if f.momento == 'devolucao']
        contexto['adicionais'] = list(self.object.adicionais.all())
        contexto['avarias'] = list(self.object.avarias.all())
        contexto['pagamentos'] = list(self.object.pagamentos.all())
        todas_parcelas = list(self.object.parcelas.all())
        contexto['parcelas'] = todas_parcelas
        contexto['notificacoes_cobranca'] = list(
            NotificacaoCobranca.objects.filter(parcela__contrato=self.object)
            .select_related('parcela', 'enviado_por')
        )
        contexto['formas_pagamento'] = ParcelaContrato.FORMAS_RECEBIMENTO
        contexto['form_adicional'] = AdicionalContratoForm()
        contexto['form_avaria'] = AvariaContratoForm()
        # Form de recebimento avulso (caução, avaria, multa, outros — NÃO locação)
        self.object.limpar_cache_financeiro()
        initial_pag = {'data_pagamento': timezone.now().strftime('%Y-%m-%dT%H:%M'), 'tipo': 'caucao'}
        form_pagamento = PagamentoContratoForm(initial=initial_pag)
        from apps.contracts.models import PagamentoContrato as _PC
        # 'locacao' → pago via PagarParcelaView (agenda)
        # 'avaria'  → pago via ContratoAvariaMarcarPagaView (sincroniza AvariaContrato.situacao)
        form_pagamento.fields['tipo'].choices = [
            c for c in _PC.TIPO if c[0] not in ('locacao', 'avaria', 'regularizacao')
        ]
        contexto['form_pagamento'] = form_pagamento
        momento = self.object.data_devolucao_real or timezone.now()
        cancelaveis = list(self.object.parcelas_futuras_abertas(momento))
        mantidas = [
            (p, p.valor) for p in self.object.parcelas_devidas_ate(momento) if p.tipo != 'caucao'
        ]
        plano_caucao = None
        if self.object.situacao == 'aguardando_devolucao':
            plano_caucao = self.object.plano_abatimento_caucao(momento)
        if plano_caucao:
            restantes = {
                i['objeto'].pk: i['restante'] for i in plano_caucao['itens']
                if isinstance(i['objeto'], ParcelaContrato)
            }
            mantidas = [(p, restantes.get(p.pk, v)) for p, v in mantidas if restantes.get(p.pk, v) > 0]
        vencidas_mantidas = [v for p, v in mantidas if p.tipo != 'acerto']
        contexto['plano_caucao'] = plano_caucao
        contexto['simulacao_prorrogacao'] = self.object.simular_prorrogacao_retroativa()
        if self.object.data_saida:
            contexto['regra_prorrogacao'] = {
                'saida': timezone.localtime(self.object.data_saida).isoformat(),
                'tolerancia_minutos': int(self.object.tolerancia_devolucao().total_seconds() // 60),
                'diaria': float(self.object.diaria_com_adicionais),
            }
        contexto['parcelas_cancelaveis_qtd'] = len(cancelaveis)
        contexto['parcelas_cancelaveis_valor'] = sum(p.valor for p in cancelaveis)
        contexto['acertos_abertos_valor'] = sum(v for p, v in mantidas if p.tipo == 'acerto')
        contexto['vencidas_mantidas_qtd'] = len(vencidas_mantidas)
        contexto['vencidas_mantidas_valor'] = sum(vencidas_mantidas)
        contexto['pode_administrar_contrato'] = tem_permissao(self.request.user, 'contratos_administrar')
        return contexto


class ContratoCheckoutView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'
    template_name = 'contracts/checkout.html'

    def _get_contrato(self, pk):
        return get_object_or_404(
            Contrato.objects.select_related('cliente', 'veiculo__grupo')
            .prefetch_related('cliente__cnhs', 'veiculo__documentos', 'pagamentos'),
            pk=pk, situacao='aberto',
        )

    def get(self, request, pk):
        contrato = self._get_contrato(pk)
        form = CheckoutForm(instance=contrato)
        return self._render(request, contrato, form)

    def post(self, request, pk):
        contrato = self._get_contrato(pk)
        form = CheckoutForm(request.POST, request.FILES, instance=contrato)
        if form.is_valid():
            # Aplica o form em memória ANTES do checklist para que caucao_situacao
            # atualizado pelo manager seja visível na validação da caução.
            contrato = form.save(commit=False)
            if contrato.caucao_situacao == 'pago' and not contrato.caucao_pago_em:
                contrato.caucao_pago_em = timezone.now()

            checklist = _checklist_saida(contrato)
            bloqueios = [msg for ok, msg in checklist if ok is False]
            if bloqueios:
                for msg in bloqueios:
                    messages.error(request, msg)
                return self._render(request, contrato, form, checklist=checklist)

            from django.db import transaction as db_transaction
            with db_transaction.atomic():
                contrato.situacao = 'ativo'
                contrato.data_saida = timezone.now()
                contrato.save()
                if contrato.km_saida is not None:
                    contrato.veiculo.km_atual = contrato.km_saida
                contrato.veiculo.situacao = 'em_uso'
                contrato.veiculo.save()
                for arquivo in request.FILES.getlist('fotos_saida'):
                    FotoContrato.objects.create(contrato=contrato, momento='saida', imagem=arquivo)
                self._registrar_caucao_no_checkout(contrato, form, request.user)
                qtd = gerar_parcelas(
                    contrato=contrato,
                    data_inicio=timezone.localdate(contrato.data_saida),
                    data_fim=contrato.fim_periodo_coberto(),
                    origem='original',
                    tipo_cobranca=contrato.forma_cobranca,
                )
            messages.success(request, f'Check-out realizado. Contrato {contrato.numero} ativo com {qtd} parcela(s) gerada(s).')
            return redirect('contratos:detalhe', pk=pk)
        return self._render(request, contrato, form)

    @staticmethod
    def _registrar_caucao_no_checkout(contrato, form, usuario):
        if contrato.caucao_valor <= 0 or contrato.parcelas.filter(tipo='caucao').exists():
            return
        contrato.limpar_cache_financeiro()
        falta = contrato.caucao_valor - contrato.total_caucao_coletado
        if falta <= 0:
            return
        parcela = ParcelaContrato.objects.create(
            contrato=contrato,
            numero=(contrato.parcelas.aggregate(m=Max('numero'))['m'] or 0) + 1,
            tipo='caucao',
            data_vencimento=timezone.localdate(contrato.data_saida),
            valor=falta,
            origem='original',
        )
        if contrato.caucao_situacao == 'pago':
            forma = form.cleaned_data.get('caucao_forma_pagamento') or 'dinheiro'
            parcela.quitar(
                falta, forma, contrato.caucao_pago_em or timezone.now(), usuario,
                'Caução coletado no check-out',
            )
        contrato.limpar_cache_financeiro()

    def _render(self, request, contrato, form, checklist=None):
        from django.shortcuts import render
        if checklist is None:
            checklist = _checklist_saida(contrato)
        return render(request, self.template_name, {
            'contrato': contrato,
            'form': form,
            'checklist': checklist,
            'tem_bloqueios': any(ok is False for ok, _ in checklist),
            'tem_avisos': any(ok is None for ok, _ in checklist),
            'regra_saida': {
                'tolerancia_minutos': int(contrato.tolerancia_devolucao().total_seconds() // 60),
                'diaria': float(contrato.diaria_com_adicionais),
            },
        })


class ContratoCheckinView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'
    template_name = 'contracts/checkin.html'

    def get(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk, situacao='ativo')
        form = CheckinForm(instance=contrato)
        return self._render(request, contrato, form)

    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk, situacao='ativo')
        form = CheckinForm(request.POST, request.FILES, instance=contrato)
        if not form.is_valid():
            return self._render(request, contrato, form)
        momento = form.cleaned_data['data_devolucao_real']
        with db_transaction.atomic():
            contrato = form.save(commit=False)
            contrato.situacao = 'aguardando_devolucao'
            contrato.calcular_fechamento()
            contrato.save()
            if contrato.km_devolucao is not None:
                contrato.veiculo.km_atual = contrato.km_devolucao
                contrato.veiculo.save()
            for arquivo in request.FILES.getlist('fotos_devolucao'):
                FotoContrato.objects.create(contrato=contrato, momento='devolucao', imagem=arquivo)
            canceladas = contrato.cancelar_parcelas_futuras(
                momento, ParcelaContrato.MARCA_CANCELADA_CHECKIN,
            )
            if canceladas:
                self._registrar_devolucao_antecipada(contrato, canceladas, request.user)
            total_acerto = _gerar_parcela_acerto_se_necessario(contrato)
            devidas = [
                p for p in contrato.parcelas_devidas_ate(momento)
                if p.tipo not in ('caucao', 'acerto')
            ]
        msg = 'Check-in realizado. Veículo aguardando vistoria e encerramento.'
        msg += self._aviso_tolerancia(contrato)
        if devidas:
            msg += (
                f' {len(devidas)} parcela(s) em aberto ({_brl(sum(p.valor_corrigido for p in devidas))}) '
                f'continuam em cobrança — o caução, se houver, abate no encerramento.'
            )
        if canceladas:
            valor_cancelado = sum(p.valor for p in canceladas)
            msg += (
                f' {len(canceladas)} parcela(s) futura(s) cancelada(s) pela devolução antecipada '
                f'({_brl(valor_cancelado)}).'
            )
        if total_acerto > 0:
            msg += f' Parcela de acerto gerada: {_brl(total_acerto)}.'
        (messages.warning if devidas or contrato.dias_extras else messages.success)(request, msg)
        return redirect('contratos:detalhe', pk=pk)

    @staticmethod
    def _registrar_devolucao_antecipada(contrato, canceladas, usuario):
        motivo = contrato.get_motivo_encerramento_display()
        if contrato.motivo_encerramento_detalhe:
            motivo += f' — {contrato.motivo_encerramento_detalhe}'
        HistoricoContrato.objects.create(
            contrato=contrato, usuario=usuario, acao='devolucao_antecipada',
            situacao_anterior='ativo', situacao_nova='aguardando_devolucao', motivo=motivo,
            dados={
                'devolucao_real': timezone.localtime(contrato.data_devolucao_real).isoformat(),
                'devolucao_prevista': timezone.localtime(contrato.data_devolucao_prevista).isoformat(),
                'parcelas_canceladas': [p.numero for p in canceladas],
                'valor_cancelado': str(sum((p.valor for p in canceladas), Decimal('0.00'))),
            },
        )

    @staticmethod
    def _aviso_tolerancia(contrato):
        tolerancia = contrato.tolerancia_devolucao()
        minutos = int(tolerancia.total_seconds() // 60)
        prazo = contrato.fim_periodo_contratado()
        if contrato.data_devolucao_real <= prazo:
            return ''
        atraso = int((contrato.data_devolucao_real - prazo).total_seconds() // 60)
        if not contrato.dias_extras:
            return (
                f' Devolução {atraso} min após o fim do período contratado, dentro da tolerância de '
                f'{minutos} min — nenhuma diária extra cobrada.'
            )
        limite = timezone.localtime(prazo + tolerancia)
        return (
            f' Devolução passou da tolerância de {minutos} min (limite {limite:%d/%m %H:%M}): '
            f'{contrato.dias_extras} diária(s) extra(s) de {_brl(contrato.diaria_com_adicionais)} '
            f'= {_brl(contrato.valor_dias_extras)} incluída(s) no acerto.'
        )

    def _render(self, request, contrato, form):
        from django.shortcuts import render
        momento = (getattr(form, 'cleaned_data', None) or {}).get('data_devolucao_real') or timezone.now()
        tolerancia = contrato.tolerancia_devolucao()
        return render(request, self.template_name, {
            'contrato': contrato,
            'form': form,
            'parcelas_abertas': contrato.parcelas_devidas_ate(momento),
            'parcelas_futuras': contrato.parcelas_futuras_abertas(momento),
            'data_corte': timezone.localdate(momento),
            'tolerancia_minutos': int(tolerancia.total_seconds() // 60),
            'limite_sem_extra': contrato.limite_devolucao_sem_extra(tolerancia),
            'regra_devolucao': _regra_devolucao(contrato, tolerancia),
        })


class ContratoEncerrarView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'
    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk, situacao='aguardando_devolucao')

        with db_transaction.atomic():
            canceladas = contrato.cancelar_parcelas_futuras(
                contrato.data_devolucao_real or timezone.now(),
                f'[cancelada no encerramento do contrato {contrato.numero}]',
            )
            plano_caucao = contrato.aplicar_caucao_no_encerramento(request.user)
            caucao_msg = _mensagem_caucao(plano_caucao)

            contrato.situacao = 'encerrado'
            contrato.save()
            # Libera o veículo — caminho normal após vistoria em 'aguardando_devolucao'
            contrato.veiculo.situacao = 'disponivel'
            contrato.veiculo.save()
            qtd_canceladas = len(canceladas)
            valor_cancelado = sum((p.valor for p in canceladas), Decimal('0'))
            mantidas = list(contrato.parcelas_abertas())
            valor_acerto_aberto = sum((p.valor for p in mantidas if p.tipo == 'acerto'), Decimal('0'))
            vencidas_mantidas = [p for p in mantidas if p.tipo != 'acerto']
            valor_vencidas_mantidas = sum((p.valor for p in vencidas_mantidas), Decimal('0'))
            contrato.limpar_cache_financeiro()
            saldo = contrato.saldo_devedor

        partes = [f'Contrato {contrato.numero} encerrado.']
        if caucao_msg:
            partes.append(caucao_msg)
        if saldo > 0:
            partes.append(f'Saldo pendente: R$ {saldo:.2f} — acompanhe em Financeiro → Contas a Receber.')
        if qtd_canceladas > 0:
            partes.append(f'{qtd_canceladas} parcela(s) cancelada(s) da agenda — R$ {valor_cancelado:.2f}.')
        if valor_acerto_aberto > 0:
            partes.append(
                f'Parcela de acerto de {_brl(valor_acerto_aberto)} continua em aberto na Agenda de Cobranças.'
            )
        if vencidas_mantidas:
            partes.append(
                f'{len(vencidas_mantidas)} parcela(s) vencida(s) de {_brl(valor_vencidas_mantidas)} '
                f'continua(m) em cobrança.'
            )
        msg = ' '.join(partes)
        if saldo > 0 or qtd_canceladas > 0 or valor_acerto_aberto > 0 or vencidas_mantidas or (plano_caucao and plano_caucao['total_abatido']):
            messages.warning(request, msg)
        else:
            messages.success(request, msg)
        return redirect('contratos:detalhe', pk=pk)


class ContratoEncerramentoAntecipadoView(PermissaoRequeridaMixin, View):
    """Encerra um contrato 'ativo' antes do prazo previsto (quebra de contrato
    — devolução antecipada do veículo).

    Reaproveita a mesma vistoria e as mesmas regras de caução/acerto do fluxo
    normal (check-in + encerrar), mas pula a etapa intermediária de
    'aguardando_devolucao': tudo é decidido em uma única confirmação, já que
    aqui não existe uma revisão humana separada entre a devolução e o
    encerramento (o próprio gestor que devolve o veículo já decide encerrar).

    Regra financeira: parcelas pagas não são tocadas; parcelas vencidas em
    aberto (vencimento <= devolução real) permanecem em cobrança, pois
    correspondem a um período em que o veículo já foi usado; só as parcelas
    futuras (vencimento > devolução real) são canceladas — nunca excluídas.
    """
    permissao = 'contratos_administrar'
    template_name = 'contracts/encerramento_antecipado.html'

    def get(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk, situacao='ativo')
        form = EncerramentoAntecipadoForm(instance=contrato)
        return self._render(request, contrato, form)

    def post(self, request, pk):
        from django.db import transaction as db_transaction

        with db_transaction.atomic():
            contrato = get_object_or_404(
                Contrato.objects.select_for_update().select_related('cliente', 'veiculo'),
                pk=pk, situacao='ativo',
            )
            form = EncerramentoAntecipadoForm(request.POST, request.FILES, instance=contrato)
            if not form.is_valid():
                return self._render(request, contrato, form)

            contrato = form.save(commit=False)
            momento = contrato.data_devolucao_real

            contrato.encerramento_antecipado = True
            contrato.encerrado_por = request.user
            contrato.encerrado_em = timezone.now()
            contrato.calcular_fechamento()
            contrato.situacao = 'encerrado'
            contrato.save()

            if contrato.km_devolucao is not None:
                contrato.veiculo.km_atual = contrato.km_devolucao
            contrato.veiculo.situacao = 'disponivel'
            contrato.veiculo.save()

            for arquivo in request.FILES.getlist('fotos_devolucao'):
                FotoContrato.objects.create(contrato=contrato, momento='devolucao', imagem=arquivo)

            total_acerto = _gerar_parcela_acerto_se_necessario(contrato)

            motivo_texto = contrato.get_motivo_encerramento_display()
            if contrato.motivo_encerramento_detalhe:
                motivo_texto += f' — {contrato.motivo_encerramento_detalhe}'

            obs_cancelamento = (
                f'[Cancelada — encerramento antecipado do contrato {contrato.numero} '
                f'em {contrato.encerrado_em:%d/%m/%Y}. Motivo: {motivo_texto}]'
            )
            canceladas = contrato.cancelar_parcelas_futuras(momento, obs_cancelamento)
            qtd_canceladas = len(canceladas)
            valor_cancelado = sum((p.valor for p in canceladas), Decimal('0'))

            plano_caucao = contrato.aplicar_caucao_no_encerramento(request.user)
            caucao_msg = _mensagem_caucao(plano_caucao)
            if plano_caucao:
                contrato.save(update_fields=['caucao_situacao'])
            parcelas_mantidas = contrato.parcelas_abertas().exclude(tipo='caucao')
            qtd_mantidas = parcelas_mantidas.count()
            valor_mantido = parcelas_mantidas.aggregate(s=Sum('valor'))['s'] or Decimal('0')

            contrato.limpar_cache_financeiro()
            saldo = contrato.saldo_devedor

            HistoricoContrato.objects.create(
                contrato=contrato,
                usuario=request.user,
                acao='encerramento_antecipado',
                situacao_anterior='ativo',
                situacao_nova='encerrado',
                motivo=motivo_texto,
                dados={
                    'parcelas_canceladas_qtd': qtd_canceladas,
                    'parcelas_canceladas_valor': str(valor_cancelado),
                    'parcelas_vencidas_mantidas_qtd': qtd_mantidas,
                    'parcelas_vencidas_mantidas_valor': str(valor_mantido),
                    'total_acerto_gerado': str(total_acerto),
                    'saldo_devedor': str(saldo),
                    'caucao_situacao': contrato.caucao_situacao,
                    'caucao_abatido': str(plano_caucao['total_abatido']) if plano_caucao else '0',
                    'caucao_devolvido': str(plano_caucao['devolver']) if plano_caucao else '0',
                },
            )

        partes = [f'Contrato {contrato.numero} encerrado antecipadamente — motivo: {motivo_texto}.']
        if total_acerto > 0:
            partes.append(f'Parcela de acerto gerada: {_brl(total_acerto)}.')
        if caucao_msg:
            partes.append(caucao_msg)
        if qtd_canceladas > 0:
            partes.append(f'{qtd_canceladas} parcela(s) futura(s) cancelada(s) — R$ {valor_cancelado:.2f}.')
        if qtd_mantidas > 0:
            partes.append(
                f'{qtd_mantidas} cobrança(s) em aberto mantida(s) — '
                f'R$ {valor_mantido:.2f}.'
            )
        if saldo > 0:
            partes.append(f'Saldo devedor: R$ {saldo:.2f} — acompanhe em Financeiro → Contas a Receber.')
        msg = ' '.join(partes)
        if saldo > 0 or qtd_mantidas > 0 or (plano_caucao and plano_caucao['total_abatido']):
            messages.warning(request, msg)
        else:
            messages.success(request, msg)
        return redirect('contratos:detalhe', pk=pk)

    def _render(self, request, contrato, form):
        from django.shortcuts import render
        # Dados enxutos das parcelas para o resumo de impacto (recalculado no
        # cliente via Alpine conforme a data de devolução digitada) — evita um
        # endpoint auxiliar só para reagir à mudança de data antes de confirmar.
        saida = timezone.localdate(contrato.data_saida)
        parcelas_resumo = [
            {
                'situacao': p.situacao,
                'tipo': p.tipo,
                'valor': float(p.valor),
                'data_vencimento': p.data_vencimento.isoformat(),
                'dia_inicio': ((p.inicio_periodo or p.data_vencimento) - saida).days,
            }
            for p in contrato.parcelas.all()
        ]
        return render(request, self.template_name, {
            'contrato': contrato,
            'form': form,
            'parcelas_resumo': parcelas_resumo,
            'regra_devolucao': _regra_devolucao(contrato),
        })


class ContratoReverterCheckinView(PermissaoRequeridaMixin, View):
    """Desfaz um check-in equivocado, revertendo o contrato de 'aguardando_devolucao'
    para 'ativo'. Exige a permissão contratos_administrar. Exclui fotos de devolução, zera
    todos os campos calculados no fechamento, e cancela a parcela de acerto
    gerada por este check-in (se ainda não paga) -- sem isso, refazer o
    check-in gera uma segunda parcela de acerto, cobrando km excedente/dias
    extras em duplicidade."""
    permissao = 'contratos_administrar'

    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk, situacao='aguardando_devolucao')
        with db_transaction.atomic():
            contrato.situacao = 'ativo'
            contrato.data_devolucao_real = None
            contrato.km_devolucao = None
            contrato.combustivel_devolucao = None
            contrato.obs_devolucao = ''
            contrato.total_dias = None
            contrato.dias_extras = 0
            contrato.km_total = None
            contrato.km_excedente = 0
            contrato.valor_km_excedente_total = Decimal('0')
            contrato.valor_dias_extras = Decimal('0')
            contrato.valor_diferenca_combustivel = Decimal('0')
            contrato.save()
            contrato.fotos.filter(momento='devolucao').delete()
            from apps.fleet.models import HistoricoKmVeiculo
            HistoricoKmVeiculo.objects.filter(contrato=contrato, origem='contrato_devolucao').delete()
            km_registrado = HistoricoKmVeiculo.objects.filter(
                veiculo=contrato.veiculo
            ).aggregate(m=Max('km'))['m']
            kms_validos = [km for km in (contrato.km_saida, km_registrado) if km is not None]
            if kms_validos:
                contrato.veiculo.km_atual = max(kms_validos)
                contrato.veiculo.save(update_fields=['km_atual'])
            # Só cancela o acerto se ainda não foi pago -- dinheiro já recebido
            # exige revisão humana, não some silenciosamente num revert.
            for parcela in contrato.parcelas.filter(
                tipo='acerto', situacao__in=['pendente', 'em_atraso']
            ):
                parcela.situacao = 'cancelada'
                parcela.save(update_fields=['situacao'])
            marca = ParcelaContrato.MARCA_CANCELADA_CHECKIN
            for parcela in contrato.parcelas.filter(
                situacao='cancelada', observacoes__contains=marca,
            ):
                parcela.situacao = 'pendente'
                parcela.observacoes = parcela.observacoes.replace(marca, '').strip()
                parcela.save(update_fields=['situacao', 'observacoes'])
        messages.warning(
            request,
            f'Check-in do contrato {contrato.numero} revertido. Contrato voltou para Ativo.'
        )
        return redirect('contratos:detalhe', pk=pk)


class ContratoCancelarView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'
    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk)
        if contrato.situacao != 'aberto':
            messages.error(
                request,
                'Só é possível cancelar contratos que ainda não tiveram saída do veículo. '
                'Para contrato ativo, use o Check-in ou a Quebra de Contrato.'
            )
            return redirect('contratos:detalhe', pk=pk)
        if contrato.pagamentos.exists():
            messages.error(
                request,
                'Este contrato já tem pagamentos registrados. Estorne-os antes de cancelar.'
            )
            return redirect('contratos:detalhe', pk=pk)
        with db_transaction.atomic():
            if contrato.veiculo.situacao == 'reservado':
                # Restaura veículo para disponivel se não há outras reservas confirmadas
                outras_reservas = Reserva.objects.filter(veiculo=contrato.veiculo, situacao='confirmada')
                if contrato.reserva_id:
                    outras_reservas = outras_reservas.exclude(pk=contrato.reserva_id)
                if not outras_reservas.exists():
                    contrato.veiculo.situacao = 'disponivel'
                    contrato.veiculo.save()
            contrato.situacao = 'cancelado'
            contrato.save()
            contrato.parcelas.filter(situacao__in=['pendente', 'em_atraso']).update(situacao='cancelada')
            HistoricoContrato.objects.create(
                contrato=contrato, usuario=request.user, acao='cancelamento',
                situacao_anterior='aberto', situacao_nova='cancelado',
            )
        messages.success(request, f'Contrato {contrato.numero} cancelado.')
        return redirect('contratos:lista')


class ContratoProrrogarView(PermissaoRequeridaMixin, View):
    """Prorroga o contrato e gera novas parcelas para o período estendido."""
    permissao = 'contratos_operar'
    def post(self, request, pk):
        from django.db import transaction as db_transaction
        from django.utils.dateparse import parse_datetime
        nova_data_str = request.POST.get('nova_data_devolucao')
        try:
            nova_data_naive = parse_datetime(nova_data_str)
            if not nova_data_naive:
                raise ValueError
            nova_data = timezone.make_aware(nova_data_naive)
        except (ValueError, TypeError):
            messages.error(request, 'Data de devolução inválida.')
            return redirect('contratos:detalhe', pk=pk)

        # select_for_update serializa prorrogacoes concorrentes do mesmo contrato --
        # um segundo submit (duplo clique ou reenvio) so le o estado apos o primeiro
        # ja ter avancado data_devolucao_prevista, e cai na checagem de data abaixo,
        # sem precisar de nenhuma regra nova para barrar a duplicata.
        with db_transaction.atomic():
            contrato = get_object_or_404(
                Contrato.objects.select_for_update(), pk=pk, situacao='ativo'
            )

            if nova_data <= contrato.data_devolucao_prevista:
                messages.error(request, 'A nova data deve ser posterior à data prevista atual.')
                return redirect('contratos:detalhe', pk=pk)

            # Bloqueia se houver reservas confirmadas do mesmo veículo no período estendido
            conflitos = Reserva.objects.filter(
                veiculo=contrato.veiculo,
                situacao__in=['pendente', 'confirmada'],
                data_retirada__lt=nova_data,
                data_devolucao__gt=contrato.data_devolucao_prevista,
            )
            if contrato.reserva_id:
                conflitos = conflitos.exclude(pk=contrato.reserva_id)
            if conflitos.exists():
                detalhes = ', '.join(
                    f'Reserva #{r.pk} ({r.cliente.nome_exibicao} — {r.data_retirada.strftime("%d/%m/%Y")})'
                    for r in conflitos[:3]
                )
                messages.error(
                    request,
                    f'Não é possível prorrogar: o veículo {contrato.veiculo.placa} tem '
                    f'reserva(s) confirmada(s) neste período: {detalhes}.'
                )
                return redirect('contratos:detalhe', pk=pk)

            hoje = timezone.localdate()
            retroativa = contrato.prorrogacao_gera_atraso()
            escolha = request.POST.get('encargos_retroativos')
            if retroativa and escolha not in ('sim', 'nao'):
                messages.error(
                    request,
                    'A devolução prevista já venceu: escolha se as parcelas do período vencido '
                    'terão multa e juros de atraso antes de prorrogar.'
                )
                return redirect('contratos:detalhe', pk=pk)

            simulacao = contrato.simular_prorrogacao_retroativa() if retroativa else None
            data_antiga = contrato.data_devolucao_prevista
            inicio_novo_periodo = contrato.fim_periodo_coberto()
            contrato.data_devolucao_prevista = nova_data
            contrato.save()
            qtd = gerar_parcelas(
                contrato=contrato,
                data_inicio=inicio_novo_periodo,
                data_fim=contrato.fim_periodo_coberto(),
                origem='prorrogacao',
                tipo_cobranca=contrato.tipo_cobranca,
                vencimento_minimo=hoje if escolha == 'nao' else None,
            )
            HistoricoContrato.objects.create(
                contrato=contrato, usuario=request.user, acao='prorrogacao',
                situacao_anterior=contrato.situacao, situacao_nova=contrato.situacao,
                motivo=(
                    '' if not retroativa
                    else 'Período vencido cobrado COM multa e juros desde os vencimentos originais.'
                    if escolha == 'sim'
                    else 'Período vencido cobrado SEM multa e juros — parcelas vencidas passaram a vencer na data da prorrogação.'
                ),
                dados={
                    'devolucao_anterior': timezone.localtime(data_antiga).isoformat(),
                    'devolucao_nova': timezone.localtime(nova_data).isoformat(),
                    'parcelas_geradas': qtd,
                    'retroativa': retroativa,
                    'encargos_retroativos': escolha if retroativa else None,
                    'encargos_estimados': str(simulacao['encargos']) if simulacao else '0',
                },
            )
        msg = f'Contrato prorrogado até {nova_data.strftime("%d/%m/%Y")}. {qtd} parcela(s) adicional(is) gerada(s).'
        if retroativa and escolha == 'sim':
            msg += ' As parcelas do período vencido já entram em atraso, com multa e juros.'
        elif retroativa:
            msg += ' As parcelas do período vencido vencem hoje, sem multa e juros.'
        messages.success(request, msg)
        return redirect('contratos:detalhe', pk=pk)


class PagarParcelaView(PermissaoRequeridaMixin, View):
    """Registra o recebimento de uma parcela.

    Valor igual ao devido (valor_corrigido) quita a parcela. Valor menor é
    pagamento parcial: a parcela é dividida e o restante continua em cobrança,
    a menos que o operador marque "conceder desconto" e justifique. Valor maior
    quita a parcela e o excedente vai para as próximas dívidas (ou vira crédito).
    """
    permissao = 'contratos_receber'

    def post(self, request, pk, parcela_pk):
        from decimal import InvalidOperation
        from django.db import IntegrityError, transaction as db_transaction

        forma = request.POST.get('forma_pagamento')
        obs = request.POST.get('observacoes', '').strip()
        desconto = request.POST.get('desconto') == '1'

        if forma not in dict(ParcelaContrato.FORMAS_RECEBIMENTO):
            messages.error(request, 'Informe a forma de pagamento.')
            return redirect('contratos:detalhe', pk=pk)

        valor_post = request.POST.get('valor', '').strip()
        valor = None
        if valor_post:
            try:
                valor = Decimal(valor_post).quantize(Decimal('0.01'))
            except (InvalidOperation, ValueError):
                valor = None
            if valor is None or valor <= 0:
                messages.error(request, 'Valor inválido.')
                return redirect('contratos:detalhe', pk=pk)

        try:
            with db_transaction.atomic():
                contrato = get_object_or_404(Contrato.objects.select_for_update(), pk=pk)
                parcela = get_object_or_404(
                    ParcelaContrato.objects.select_for_update(), pk=parcela_pk,
                    contrato=contrato, situacao__in=['pendente', 'em_atraso'],
                )
                devido = parcela.valor_corrigido
                valor = valor if valor is not None else devido
                agora = timezone.now()
                rotulo = parcela.rotulo

                if valor < devido and desconto:
                    if not obs:
                        messages.error(
                            request,
                            f'Para conceder desconto (receber {_brl(valor)} de {_brl(devido)}), '
                            f'informe o motivo em Observações.'
                        )
                        return redirect('contratos:detalhe', pk=pk)
                    parcela.quitar_com_desconto(valor, forma, agora, request.user, obs)
                    msg = f'{rotulo} quitada com desconto de {_brl(devido - valor)} — recebido {_brl(valor)}.'
                elif valor < devido:
                    parcela.quitar(valor, forma, agora, request.user, obs)
                    msg = (
                        f'Pagamento parcial de {_brl(valor)} registrado na {rotulo.lower()}. '
                        f'O restante ({_brl(devido - valor)}) continua em cobrança.'
                    )
                else:
                    parcela.quitar(devido, forma, agora, request.user, obs)
                    msg = f'{rotulo} registrada como paga — {_brl(devido)}.'
                    excedente = valor - devido
                    if excedente > 0:
                        contrato.receber_em_ordem(
                            excedente, forma, agora, request.user,
                            f'Excedente do pagamento da {rotulo.lower()}. {obs}'.strip(),
                        )
                        msg += f' Excedente de {_brl(excedente)} aplicado nas próximas dívidas (ou mantido como crédito).'

                if parcela.tipo == 'caucao':
                    contrato.atualizar_situacao_caucao(agora)
        except IntegrityError:
            messages.error(
                request,
                'Esta parcela já foi paga por outro lançamento simultâneo. Atualize a página.'
            )
            return redirect('contratos:detalhe', pk=pk)

        messages.success(request, msg)
        return redirect('contratos:detalhe', pk=pk)


class ContratoAdicionaisView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'
    template_name = 'contracts/adicionais.html'

    def get(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk)
        form = AdicionalContratoForm()
        return self._render(request, contrato, form)

    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk)
        if contrato.situacao not in ('aberto', 'ativo'):
            messages.error(
                request,
                'Adicionais só podem ser incluídos antes do check-in — '
                'depois disso não há parcelas futuras para cobrá-los.'
            )
            return redirect('contratos:detalhe', pk=pk)
        form = AdicionalContratoForm(request.POST)
        if form.is_valid():
            with db_transaction.atomic():
                adicional = form.save(commit=False)
                adicional.contrato = contrato
                adicional.save()
                alteradas = contrato.reprecificar_parcelas_futuras() if contrato.situacao == 'ativo' else 0
            msg = 'Adicional incluído.'
            if alteradas:
                msg += f' {alteradas} parcela(s) futura(s) em aberto atualizada(s) com o novo valor diário.'
            messages.success(request, msg)
            return redirect('contratos:detalhe', pk=pk)
        return self._render(request, contrato, form)

    def _render(self, request, contrato, form):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'contrato': contrato,
            'adicionais': contrato.adicionais.all(),
            'form': form,
            'pode_remover': contrato.situacao in Contrato.SITUACOES_COM_ADICIONAIS_EDITAVEIS,
        })


class ContratoAvariasView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'
    template_name = 'contracts/avarias.html'

    def get(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk)
        form = AvariaContratoForm()
        return self._render(request, contrato, form)

    def post(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk)
        form = AvariaContratoForm(request.POST, request.FILES)
        if form.is_valid():
            avaria = form.save(commit=False)
            avaria.contrato = contrato
            avaria.save()
            messages.success(request, 'Avaria registrada.')
            return redirect('contratos:detalhe', pk=pk)
        return self._render(request, contrato, form)

    def _render(self, request, contrato, form):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'contrato': contrato,
            'avarias': contrato.avarias.all(),
            'form': form,
            'formas_pagamento': PagamentoContrato.FORMAS_RECEBIMENTO,
            'situacoes_ajuste': [c for c in AvariaContrato.SITUACAO if c[0] in AvariaContrato.SITUACOES_AJUSTAVEIS],
        })


class ContratoAvariaMarcarPagaView(PermissaoRequeridaMixin, View):
    """Marca uma avaria como paga e registra o PagamentoContrato atomicamente."""
    permissao = 'contratos_receber'

    def post(self, request, pk, avaria_pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk)
        avaria = get_object_or_404(AvariaContrato, pk=avaria_pk, contrato=contrato, situacao='cobrada')

        if not avaria.valor_cobrado:
            messages.error(request, 'Avaria sem valor definido. Defina o valor antes de marcar como paga.')
            return redirect('contratos:avarias', pk=pk)

        forma = request.POST.get('forma_pagamento')
        if forma not in dict(PagamentoContrato.FORMAS_RECEBIMENTO):
            messages.error(request, 'Informe a forma de pagamento.')
            return redirect('contratos:avarias', pk=pk)

        from django.db import IntegrityError
        try:
            with db_transaction.atomic():
                avaria.situacao = 'paga'
                avaria.save(update_fields=['situacao'])
                PagamentoContrato.objects.create(
                    contrato=contrato,
                    avaria=avaria,
                    forma_pagamento=forma,
                    tipo='avaria',
                    valor=avaria.valor_cobrado,
                    registrado_por=request.user,
                    observacoes=f'Avaria: {avaria.descricao[:100]}',
                )
        except IntegrityError:
            messages.error(
                request,
                'Esta avaria já foi paga por outro lançamento simultâneo. Atualize a página.'
            )
            return redirect('contratos:avarias', pk=pk)

        messages.success(request, f'Avaria registrada como paga — R$ {avaria.valor_cobrado:.2f}.')
        return redirect('contratos:avarias', pk=pk)


class ContratoAvariaAjustarView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_receber'

    def post(self, request, pk, avaria_pk):
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            avaria = get_object_or_404(
                AvariaContrato.objects.select_for_update().select_related('contrato'),
                pk=avaria_pk, contrato_id=pk,
            )
            if not avaria.pode_ajustar:
                messages.error(request, 'Avaria já paga não pode ser alterada.')
                return redirect('contratos:avarias', pk=pk)
            form = AjusteAvariaForm(request.POST)
            if not form.is_valid():
                erros = '; '.join(e for lista in form.errors.values() for e in lista)
                messages.error(request, f'Não foi possível alterar a avaria: {erros}')
                return redirect('contratos:avarias', pk=pk)
            alterou = avaria.ajustar(form.cleaned_data['valor_cobrado'], form.cleaned_data['situacao'], request.user)
        if alterou:
            messages.success(
                request,
                f'Avaria atualizada: {_brl(avaria.valor_cobrado)}, {avaria.get_situacao_display().lower()}.',
            )
        else:
            messages.info(request, 'Nada foi alterado na avaria.')
        return redirect('contratos:avarias', pk=pk)


class ContratoAdicionalRemoverView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_operar'

    def post(self, request, pk, adicional_pk):
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            contrato = get_object_or_404(Contrato.objects.select_for_update(), pk=pk)
            adicional = get_object_or_404(AdicionalContrato, pk=adicional_pk, contrato=contrato)
            try:
                alteradas = contrato.remover_adicional(adicional, request.user)
            except ValueError as erro:
                messages.error(request, str(erro))
                return redirect('contratos:adicionais', pk=pk)
        msg = f'Adicional {adicional.get_tipo_display()} removido.'
        if alteradas:
            msg += f' {alteradas} parcela(s) futura(s) em aberto recalculada(s).'
        messages.success(request, msg)
        return redirect('contratos:adicionais', pk=pk)


class ContratoPDFView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_consultar'
    def get(self, request, pk):
        from io import BytesIO
        from xhtml2pdf import pisa
        from django.template.loader import render_to_string

        contrato = get_object_or_404(Contrato, pk=pk)
        html = render_to_string('contracts/pdf_contrato.html', {
            'contrato': contrato,
            'adicionais': contrato.adicionais.all(),
        }, request=request)

        buffer = BytesIO()
        pisa.pisaDocument(BytesIO(html.encode('utf-8')), buffer)
        pdf = buffer.getvalue()
        buffer.close()

        resposta = HttpResponse(pdf, content_type='application/pdf')
        resposta['Content-Disposition'] = f'inline; filename="contrato-{contrato.numero}.pdf"'
        return resposta


class AssinaturaView(View):
    """View pública para assinatura digital do contrato via link."""
    template_name = 'contracts/assinatura.html'

    def get(self, request, token):
        contrato = get_object_or_404(Contrato, token_assinatura=token)
        return self._render(request, contrato)

    def post(self, request, token):
        contrato = get_object_or_404(Contrato, token_assinatura=token)
        if contrato.assinado:
            messages.info(request, 'Este contrato já foi assinado.')
            return self._render(request, contrato)
        contrato.assinado_em = timezone.now()
        contrato.ip_assinatura = self._obter_ip(request)
        contrato.save()
        messages.success(request, 'Contrato assinado com sucesso!')
        return self._render(request, contrato)

    def _render(self, request, contrato):
        from django.shortcuts import render
        return render(request, self.template_name, {'contrato': contrato})

    def _obter_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class ContratoPagamentosView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_receber'
    template_name = 'contracts/pagamentos.html'

    def get(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk)
        return self._render(request, contrato)

    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk)
        form = PagamentoContratoForm(request.POST)
        if form.is_valid():
            dados = form.cleaned_data
            with db_transaction.atomic():
                contrato = get_object_or_404(Contrato.objects.select_for_update(), pk=pk)
                parcelas_baixadas = contrato.registrar_recebimento(
                    dados['tipo'], dados['valor'], dados['forma_pagamento'], dados['data_pagamento'],
                    request.user, dados.get('observacoes', ''),
                )
            msg = f'Pagamento de {_brl(dados["valor"])} registrado com sucesso.'
            if parcelas_baixadas:
                msg += f' Aplicado em {parcelas_baixadas} parcela(s), da mais antiga para a mais nova.'
            messages.success(request, msg)
            return redirect('contratos:detalhe', pk=pk)
        for field, erros in form.errors.items():
            label = form.fields[field].label if field in form.fields else field
            for erro in erros:
                messages.error(request, f'{label}: {erro}')
        return redirect('contratos:detalhe', pk=pk)

    def _render(self, request, contrato, form=None):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'contrato': contrato,
            'pagamentos': contrato.pagamentos.select_related('registrado_por'),
            'pode_estornar': _pode_criar_retroativo(request.user),
        })


class ContratoPagamentoEstornarView(PermissaoRequeridaMixin, View):
    permissao = 'contratos_administrar'

    def post(self, request, pk, pagamento_pk):
        from django.db import transaction as db_transaction
        motivo = request.POST.get('motivo', '').strip()
        if not motivo:
            messages.error(request, 'Informe o motivo do estorno.')
            return redirect('contratos:pagamentos', pk=pk)
        with db_transaction.atomic():
            contrato = get_object_or_404(Contrato.objects.select_for_update(), pk=pk)
            pagamento = get_object_or_404(PagamentoContrato, pk=pagamento_pk, contrato=contrato)
            erro = pagamento.erro_ao_estornar()
            if erro:
                messages.error(request, erro)
                return redirect('contratos:pagamentos', pk=pk)
            valor = pagamento.valor
            pagamento.estornar(request.user, motivo)
        messages.warning(
            request,
            f'Pagamento de {_brl(valor)} estornado. A cobrança correspondente voltou a ficar em aberto.'
        )
        return redirect('contratos:pagamentos', pk=pk)
