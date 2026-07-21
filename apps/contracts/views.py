from decimal import Decimal

from django.contrib import messages
from apps.core.mixins import GrupoRequiredMixin
from django.db.models import Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView, View

from .forms import (ReservaForm, ContratoForm, CheckoutForm, CheckinForm,
                    EncerramentoAntecipadoForm, AdicionalContratoForm,
                    AvariaContratoForm, PagamentoContratoForm)
from .models import (AdicionalContrato, AvariaContrato, Contrato, FotoContrato,
                     HistoricoContrato, PagamentoContrato, ParcelaContrato,
                     Reserva, gerar_parcelas, proximo_vencimento_contratual)
from apps.fleet.models import Veiculo


def _brl(v):
    """Formata Decimal/float como BRL para uso em strings Python (não em templates)."""
    return 'R$ ' + f'{float(v or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _checklist_saida(contrato):
    """Retorna lista de (ok: bool|None, mensagem: str) com o checklist de saída
    do veículo — usado tanto no check-out normal quanto na criação retroativa."""
    hoje = timezone.now().date()
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
    dias_contrato = max(contrato._dias_previstos(), 1)
    dias_cobranca = min(7, dias_contrato)
    valor_primeira_semana = contrato.diaria * dias_cobranca
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
    """Somente admin_locadora (ou superuser) pode criar contrato retroativo/migrado —
    essa via gera um pagamento de regularização sem lastro bancário conferível."""
    if user.is_superuser:
        return True
    grupos = getattr(user, '_grupos_nomes_cache', None)
    if grupos is None:
        grupos = set(user.groups.values_list('name', flat=True))
    return 'admin_locadora' in grupos


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
        partes_obs.append(
            f'{contrato.dias_extras} dia(s) extra(s) '
            f'× {_brl(contrato.diaria)} = {_brl(contrato.valor_dias_extras)}'
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
        data_vencimento=contrato.data_devolucao_real.date(),
        valor=total_acerto,
        origem='original',
        situacao='pendente',
        observacoes=' | '.join(partes_obs),
    )
    return total_acerto


def _avaliar_caucao_no_encerramento(contrato, usuario):
    """Avalia retenção/devolução do caução ao encerrar o contrato (normal ou
    antecipado). Sem avaria cobrada, devolve o caução inteiro automaticamente;
    com avaria cobrada, retém só o valor efetivo do dano (nunca mais que o
    caução) e devolve o excedente. Muda contrato.caucao_situacao em memória
    (quem chama ainda precisa salvar) e retorna uma mensagem descritiva, ou
    None se não havia caução retido a avaliar.
    """
    if not (contrato.caucao_valor and contrato.caucao_situacao == 'retido'):
        return None

    valor_avarias_cobradas = contrato.avarias.filter(
        situacao='cobrada'
    ).aggregate(s=Sum('valor_cobrado'))['s'] or Decimal('0')

    if valor_avarias_cobradas <= 0:
        # Sem avaria cobrada (ou só identificada, sem valor definido):
        # devolve o caução inteiro automaticamente.
        # O signal contrato_caucao_post_save cria a DespesaOperacional.
        contrato.caucao_situacao = 'devolvido'
        return (
            f'Caução de R$ {contrato.caucao_valor:.2f} devolvido automaticamente '
            f'— nenhuma avaria cobrada registrada.'
        )

    # Com avaria cobrada: retém só o valor efetivo do dano (nunca mais
    # que o caução) e devolve o excedente automaticamente, se houver.
    valor_retido = min(contrato.caucao_valor, valor_avarias_cobradas)
    valor_devolvido = contrato.caucao_valor - valor_retido
    marker = f'[caucao_retido:{contrato.numero}]'
    if not PagamentoContrato.objects.filter(observacoes__contains=marker).exists():
        PagamentoContrato.objects.create(
            contrato=contrato,
            tipo='avaria',
            forma_pagamento='dinheiro',
            valor=valor_retido,
            data_pagamento=timezone.now(),
            registrado_por=usuario,
            observacoes=f'{marker} Caucao retido — aplicado na cobertura de avarias',
        )
    if valor_devolvido > 0:
        contrato.caucao_situacao = 'devolvido_parcial'
        contrato._caucao_valor_devolvido = valor_devolvido
        return (
            f'Caução: R$ {valor_retido:.2f} retido para cobertura de avarias, '
            f'R$ {valor_devolvido:.2f} devolvido automaticamente.'
        )
    return (
        f'Caução de R$ {contrato.caucao_valor:.2f} retido integralmente '
        f'— avarias cobradas somam R$ {valor_avarias_cobradas:.2f} ou mais.'
    )


# ─── Reservas ────────────────────────────────────────────────────────────────

class ReservaListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'atendente']
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


class ReservaCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora', 'atendente']
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


class ReservaUpdateView(GrupoRequiredMixin, UpdateView):
    grupos_permitidos = ['admin_locadora', 'atendente']
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


class ReservaDeleteView(GrupoRequiredMixin, DeleteView):
    grupos_permitidos = ['admin_locadora']
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


class ReservaDetailView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'atendente']
    model = Reserva
    template_name = 'contracts/reserva_detalhe.html'
    context_object_name = 'reserva'


class ReservaConfirmarView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
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


class ReservaCancelarView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
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


class ReservaNoShowView(GrupoRequiredMixin, View):
    """Marca uma reserva como no_show e libera o veículo se aplicável."""
    grupos_permitidos = ['admin_locadora', 'atendente']
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


class ReservaConverterView(GrupoRequiredMixin, View):
    """Converte uma reserva confirmada em contrato aberto."""
    grupos_permitidos = ['admin_locadora', 'atendente']
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

        with transaction.atomic():
            contrato = Contrato.objects.create(
                reserva=reserva,
                cliente=reserva.cliente,
                veiculo=reserva.veiculo,
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

class ContratoListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'financeiro']
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


class ContratoCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora', 'atendente']
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
        agora = timezone.now()
        hoje = agora.date()

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
            if valor_ja_recebido > 0:
                PagamentoContrato.objects.create(
                    contrato=contrato,
                    tipo='locacao',
                    forma_pagamento='dinheiro',
                    valor=valor_ja_recebido,
                    data_pagamento=agora,
                    registrado_por=self.request.user,
                    observacoes=(
                        f'Regularização de saldo pré-sistema — período de '
                        f'{contrato.data_saida.strftime("%d/%m/%Y")} a {hoje.strftime("%d/%m/%Y")}. '
                        f'Justificativa: {contrato.obs_saida}'
                    ),
                )

            if contrato.caucao_valor > 0:
                ja_pago = contrato.caucao_situacao == 'pago'
                if ja_pago:
                    PagamentoContrato.objects.create(
                        contrato=contrato,
                        tipo='caucao',
                        forma_pagamento='dinheiro',
                        valor=contrato.caucao_valor,
                        data_pagamento=agora,
                        registrado_por=self.request.user,
                        observacoes=(
                            f'Caução coletado no registro retroativo/migração '
                            f'(referente à saída em {contrato.data_saida.strftime("%d/%m/%Y")})'
                        ),
                    )
                ParcelaContrato.objects.create(
                    contrato=contrato,
                    numero=1,
                    tipo='caucao',
                    data_vencimento=contrato.data_saida.date(),
                    valor=contrato.caucao_valor,
                    origem='original',
                    situacao='pago' if ja_pago else 'pendente',
                    data_pagamento=contrato.data_saida if ja_pago else None,
                )

            # Parcelas de locação: primeira parcela em aberto é o próximo
            # vencimento do cronograma real do contrato (ancorado em data_saida),
            # não a data de hoje — o período já vencido continua resolvido pelo
            # pagamento de regularização acima, não deve virar parcela retroativa.
            grupo = contrato.veiculo.grupo
            tipo_cobranca = 'mensal' if (grupo and grupo.mensal) else 'semanal'
            data_inicio_cobranca = proximo_vencimento_contratual(
                contrato.data_saida.date(), hoje, tipo_cobranca,
            )
            gerar_parcelas(
                contrato=contrato,
                data_inicio=data_inicio_cobranca,
                data_fim=contrato.data_devolucao_prevista.date(),
                origem='original',
                tipo_cobranca=tipo_cobranca,
            )

        for ok, msg in _checklist_saida(contrato):
            if ok is False:
                messages.warning(self.request, msg)
        messages.success(
            self.request,
            f'Contrato {contrato.numero} criado e ativado (registro retroativo/migração).'
        )

        self.object = contrato
        return redirect(self.get_success_url())


class ContratoDetailView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'financeiro']
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
        contexto['formas_pagamento'] = ParcelaContrato.FORMA
        contexto['form_adicional'] = AdicionalContratoForm()
        contexto['form_avaria'] = AvariaContratoForm()
        # Form de recebimento avulso (caução, avaria, multa, outros — NÃO locação)
        for attr in ('total_geral', 'total_pago', 'saldo_devedor'):
            self.object.__dict__.pop(attr, None)
        initial_pag = {'data_pagamento': timezone.now().strftime('%Y-%m-%dT%H:%M'), 'tipo': 'caucao'}
        form_pagamento = PagamentoContratoForm(initial=initial_pag)
        from apps.contracts.models import PagamentoContrato as _PC
        # 'locacao' → pago via PagarParcelaView (agenda)
        # 'avaria'  → pago via ContratoAvariaMarcarPagaView (sincroniza AvariaContrato.situacao)
        form_pagamento.fields['tipo'].choices = [
            c for c in _PC.TIPO if c[0] not in ('locacao', 'avaria')
        ]
        contexto['form_pagamento'] = form_pagamento
        cancelaveis = [p for p in todas_parcelas if p.situacao in ('pendente', 'em_atraso')]
        contexto['parcelas_cancelaveis_qtd'] = len(cancelaveis)
        contexto['parcelas_cancelaveis_valor'] = sum(p.valor for p in cancelaveis)
        # Usa cache de grupos setado pelo GrupoRequiredMixin.dispatch()
        grupos = getattr(self.request.user, '_grupos_nomes_cache', None)
        if grupos is None:
            grupos = set(self.request.user.groups.values_list('name', flat=True))
        # Superusuario sempre pode -- mesmo criterio de bypass do GrupoRequiredMixin,
        # senao um superuser sem o grupo 'admin_locadora' explicito nao veria botoes
        # (Quebra de Contrato, Reverter Check-in) que ele tem permissao de acessar.
        contexto['is_admin_locadora'] = self.request.user.is_superuser or 'admin_locadora' in grupos
        return contexto


class ContratoCheckoutView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
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
                # Caução: cria PagamentoContrato se foi coletado agora no checkout
                if (contrato.caucao_valor > 0 and contrato.caucao_situacao == 'pago'
                        and not contrato.pagamentos.filter(tipo='caucao').exists()):
                    forma_caucao = form.cleaned_data.get('caucao_forma_pagamento') or 'dinheiro'
                    PagamentoContrato.objects.create(
                        contrato=contrato,
                        tipo='caucao',
                        forma_pagamento=forma_caucao,
                        valor=contrato.caucao_valor,
                        data_pagamento=contrato.caucao_pago_em,
                        registrado_por=request.user,
                        observacoes='Caução coletado no check-out',
                    )
                # Parcela de caução: cria se ainda não existe (evita duplicata em re-submit)
                if contrato.caucao_valor > 0 and not contrato.parcelas.filter(tipo='caucao').exists():
                    ja_pago = contrato.caucao_situacao == 'pago'
                    ParcelaContrato.objects.create(
                        contrato=contrato,
                        numero=1,
                        tipo='caucao',
                        data_vencimento=contrato.data_saida.date(),
                        valor=contrato.caucao_valor,
                        origem='original',
                        situacao='pago' if ja_pago else 'pendente',
                        data_pagamento=contrato.caucao_pago_em or (timezone.now() if ja_pago else None),
                    )
                grupo = contrato.veiculo.grupo
                tipo_cobranca = 'mensal' if (grupo and grupo.mensal) else 'semanal'
                qtd = gerar_parcelas(
                    contrato=contrato,
                    data_inicio=contrato.data_saida.date(),
                    data_fim=contrato.data_devolucao_prevista.date(),
                    origem='original',
                    tipo_cobranca=tipo_cobranca,
                )
            messages.success(request, f'Check-out realizado. Contrato {contrato.numero} ativo com {qtd} parcela(s) gerada(s).')
            return redirect('contratos:detalhe', pk=pk)
        return self._render(request, contrato, form)

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
        })


class ContratoCheckinView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
    template_name = 'contracts/checkin.html'

    def get(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk, situacao='ativo')
        form = CheckinForm(instance=contrato)
        return self._render(request, contrato, form)

    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk, situacao='ativo')
        parcelas_abertas = contrato.parcelas.filter(
            situacao__in=['pendente', 'em_atraso']
        ).order_by('data_vencimento')
        if parcelas_abertas.exists():
            form = CheckinForm(instance=contrato)
            messages.error(request, 'Quite as parcelas em aberto antes de realizar o check-in.')
            return self._render(request, contrato, form, parcelas_abertas)
        form = CheckinForm(request.POST, request.FILES, instance=contrato)
        if form.is_valid():
            with db_transaction.atomic():
                contrato = form.save(commit=False)
                contrato.situacao = 'aguardando_devolucao'
                contrato.calcular_fechamento()
                contrato.save()
                # Atualiza KM mas mantém veículo em 'em_uso' até o contrato ser encerrado
                # (liberado em ContratoEncerrarView, após avarias e fechamento avaliados)
                if contrato.km_devolucao is not None:
                    contrato.veiculo.km_atual = contrato.km_devolucao
                    contrato.veiculo.save()
                # Salva fotos de devolução
                for arquivo in request.FILES.getlist('fotos_devolucao'):
                    FotoContrato.objects.create(contrato=contrato, momento='devolucao', imagem=arquivo)
                # Gera parcela de acerto final se houver km excedente, dias extras ou diferença de combustível
                total_acerto = _gerar_parcela_acerto_se_necessario(contrato)
            msg = 'Check-in realizado. Veículo aguardando vistoria e encerramento.'
            if total_acerto > 0:
                msg += f' Parcela de acerto gerada: {_brl(total_acerto)}.'
            messages.success(request, msg)
            return redirect('contratos:detalhe', pk=pk)
        return self._render(request, contrato, form)

    def _render(self, request, contrato, form, parcelas_abertas=None):
        from django.shortcuts import render
        if parcelas_abertas is None:
            parcelas_abertas = contrato.parcelas.filter(
                situacao__in=['pendente', 'em_atraso']
            ).order_by('data_vencimento')
        return render(request, self.template_name, {
            'contrato': contrato,
            'form': form,
            'parcelas_abertas': parcelas_abertas,
        })


class ContratoEncerrarView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk, situacao='aguardando_devolucao')
        # Garante cálculo fresco antes de verificar saldo devedor
        for attr in ('total_geral', 'total_adicionais', 'total_avarias',
                     'total_locacao', 'total_pago', 'total_caucao_coletado', 'saldo_devedor'):
            contrato.__dict__.pop(attr, None)
        saldo = contrato.saldo_devedor

        with db_transaction.atomic():
            # ── Avaliação do caução retido no encerramento ───────────────────
            caucao_msg = _avaliar_caucao_no_encerramento(contrato, request.user)

            contrato.situacao = 'encerrado'
            contrato.save()
            # Libera o veículo — caminho normal após vistoria em 'aguardando_devolucao'
            contrato.veiculo.situacao = 'disponivel'
            contrato.veiculo.save()
            # Cancela parcelas pendentes e registra o impacto para a mensagem
            parcelas_qs = contrato.parcelas.filter(situacao__in=['pendente', 'em_atraso'])
            qtd_canceladas = parcelas_qs.count()
            from django.db.models import Sum as _Sum
            valor_cancelado = parcelas_qs.aggregate(s=_Sum('valor'))['s'] or Decimal('0')
            parcelas_qs.update(situacao='cancelada')

        partes = [f'Contrato {contrato.numero} encerrado.']
        if caucao_msg:
            partes.append(caucao_msg)
        if saldo > 0:
            partes.append(f'Saldo pendente: R$ {saldo:.2f} — acompanhe em Financeiro → Contas a Receber.')
        if qtd_canceladas > 0:
            partes.append(f'{qtd_canceladas} parcela(s) cancelada(s) da agenda — R$ {valor_cancelado:.2f}.')
        msg = ' '.join(partes)
        if saldo > 0 or qtd_canceladas > 0 or (caucao_msg and 'retido' in caucao_msg):
            messages.warning(request, msg)
        else:
            messages.success(request, msg)
        return redirect('contratos:detalhe', pk=pk)


class ContratoEncerramentoAntecipadoView(GrupoRequiredMixin, View):
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
    grupos_permitidos = ['admin_locadora']
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
            data_corte = contrato.data_devolucao_real.date()

            contrato.encerramento_antecipado = True
            contrato.encerrado_por = request.user
            contrato.encerrado_em = timezone.now()
            contrato.calcular_fechamento()
            caucao_msg = _avaliar_caucao_no_encerramento(contrato, request.user)
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

            # Só as parcelas futuras (vencimento após a devolução real) deixam de valer —
            # parcelas vencidas em aberto continuam em cobrança normalmente.
            parcelas_abertas = contrato.parcelas.filter(situacao__in=['pendente', 'em_atraso'])
            parcelas_futuras = parcelas_abertas.filter(data_vencimento__gt=data_corte)
            parcelas_mantidas = parcelas_abertas.filter(data_vencimento__lte=data_corte)

            qtd_mantidas = parcelas_mantidas.count()
            valor_mantido = parcelas_mantidas.aggregate(s=Sum('valor'))['s'] or Decimal('0')

            qtd_canceladas = parcelas_futuras.count()
            valor_cancelado = parcelas_futuras.aggregate(s=Sum('valor'))['s'] or Decimal('0')
            obs_cancelamento = (
                f'Cancelada — encerramento antecipado do contrato {contrato.numero} '
                f'em {contrato.encerrado_em:%d/%m/%Y}. Motivo: {motivo_texto}'
            )
            for parcela in parcelas_futuras:
                parcela.situacao = 'cancelada'
                parcela.observacoes = (
                    f'{parcela.observacoes} | {obs_cancelamento}' if parcela.observacoes
                    else obs_cancelamento
                )
                parcela.save(update_fields=['situacao', 'observacoes'])

            for attr in ('total_geral', 'total_adicionais', 'total_avarias',
                         'total_locacao', 'total_pago', 'total_caucao_coletado', 'saldo_devedor'):
                contrato.__dict__.pop(attr, None)
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
                f'{qtd_mantidas} parcela(s) vencida(s) mantida(s) em aberto para cobrança — '
                f'R$ {valor_mantido:.2f}.'
            )
        if saldo > 0:
            partes.append(f'Saldo devedor: R$ {saldo:.2f} — acompanhe em Financeiro → Contas a Receber.')
        msg = ' '.join(partes)
        if saldo > 0 or qtd_mantidas > 0 or (caucao_msg and 'retido' in caucao_msg):
            messages.warning(request, msg)
        else:
            messages.success(request, msg)
        return redirect('contratos:detalhe', pk=pk)

    def _render(self, request, contrato, form):
        from django.shortcuts import render
        # Dados enxutos das parcelas para o resumo de impacto (recalculado no
        # cliente via Alpine conforme a data de devolução digitada) — evita um
        # endpoint auxiliar só para reagir à mudança de data antes de confirmar.
        parcelas_resumo = [
            {
                'situacao': p.situacao,
                'valor': float(p.valor),
                'data_vencimento': p.data_vencimento.isoformat(),
            }
            for p in contrato.parcelas.all()
        ]
        return render(request, self.template_name, {
            'contrato': contrato,
            'form': form,
            'parcelas_resumo': parcelas_resumo,
            'data_corte_inicial': timezone.localtime(timezone.now()).date().isoformat(),
        })


class ContratoReverterCheckinView(GrupoRequiredMixin, View):
    """Desfaz um check-in equivocado, revertendo o contrato de 'aguardando_devolucao'
    para 'ativo'. Restrito a admin_locadora. Exclui fotos de devolução, zera
    todos os campos calculados no fechamento, e cancela a parcela de acerto
    gerada por este check-in (se ainda não paga) -- sem isso, refazer o
    check-in gera uma segunda parcela de acerto, cobrando km excedente/dias
    extras em duplicidade."""
    grupos_permitidos = ['admin_locadora']

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
            # Só cancela o acerto se ainda não foi pago -- dinheiro já recebido
            # exige revisão humana, não some silenciosamente num revert.
            contrato.parcelas.filter(
                tipo='acerto', situacao__in=['pendente', 'em_atraso']
            ).update(situacao='cancelada')
        messages.warning(
            request,
            f'Check-in do contrato {contrato.numero} revertido. Contrato voltou para Ativo.'
        )
        return redirect('contratos:detalhe', pk=pk)


class ContratoCancelarView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk, situacao__in=['aberto', 'ativo'])
        with db_transaction.atomic():
            if contrato.situacao == 'ativo':
                contrato.veiculo.situacao = 'disponivel'
                contrato.veiculo.save()
            elif contrato.situacao == 'aberto' and contrato.veiculo.situacao == 'reservado':
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
        messages.success(request, f'Contrato {contrato.numero} cancelado.')
        return redirect('contratos:lista')


class ContratoProrrogarView(GrupoRequiredMixin, View):
    """Prorroga o contrato e gera novas parcelas para o período estendido."""
    grupos_permitidos = ['admin_locadora', 'atendente']
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

            data_antiga = contrato.data_devolucao_prevista
            grupo = contrato.veiculo.grupo if contrato.veiculo_id else None
            tipo_cobranca = 'mensal' if (grupo and grupo.mensal) else 'semanal'
            contrato.data_devolucao_prevista = nova_data
            contrato.save()
            qtd = gerar_parcelas(
                contrato=contrato,
                data_inicio=data_antiga.date(),
                data_fim=nova_data.date(),
                origem='prorrogacao',
                tipo_cobranca=tipo_cobranca,
            )
        messages.success(
            request,
            f'Contrato prorrogado até {nova_data.strftime("%d/%m/%Y")}. '
            f'{qtd} parcela(s) adicional(is) gerada(s).'
        )
        return redirect('contratos:detalhe', pk=pk)


class PagarParcelaView(GrupoRequiredMixin, View):
    """Registra o pagamento de uma parcela e cria o PagamentoContrato correspondente.

    Aplica valor_corrigido (com multa/juros) quando a parcela está em atraso.
    O operador pode informar um valor customizado via POST; se ausente, usa valor_corrigido.
    """
    grupos_permitidos = ['admin_locadora', 'atendente', 'financeiro']
    def post(self, request, pk, parcela_pk):
        from decimal import InvalidOperation
        from django.db import transaction as db_transaction

        contrato = get_object_or_404(Contrato, pk=pk)
        parcela = get_object_or_404(
            ParcelaContrato, pk=parcela_pk,
            contrato=contrato, situacao__in=['pendente', 'em_atraso']
        )
        forma = request.POST.get('forma_pagamento')
        obs = request.POST.get('observacoes', '')

        if not forma:
            messages.error(request, 'Informe a forma de pagamento.')
            return redirect('contratos:detalhe', pk=pk)

        # Usa valor informado pelo operador ou cai no valor_corrigido (inclui multa/juros)
        valor_post = request.POST.get('valor', '').strip()
        try:
            from decimal import Decimal as _D
            valor = _D(valor_post).quantize(_D('0.01'))
            if valor <= 0:
                raise ValueError
        except (ValueError, TypeError, InvalidOperation):
            valor = parcela.valor_corrigido

        # Valor diferente do devido nunca é aceito silenciosamente — exige que o
        # operador justifique a divergência em Observações antes de confirmar.
        if valor != parcela.valor_corrigido and not obs.strip():
            messages.error(
                request,
                f'O valor informado (R$ {valor:.2f}) diverge do valor devido '
                f'(R$ {parcela.valor_corrigido:.2f}). Informe uma observação '
                f'justificando a diferença para confirmar o pagamento.'
            )
            return redirect('contratos:detalhe', pk=pk)

        # Observação automática que registra multa/juros aplicados para rastreabilidade
        if not obs:
            if valor > parcela.valor:
                multa_aplicada = valor - parcela.valor
                obs = (f'Parcela {parcela.numero} — {parcela.get_tipo_display()} '
                       f'(R$ {parcela.valor:.2f} original + R$ {multa_aplicada:.2f} multa/juros)')
            else:
                obs = f'Parcela {parcela.numero} — {parcela.get_tipo_display()}'

        from django.db import IntegrityError
        try:
            with db_transaction.atomic():
                parcela.situacao = 'pago'
                parcela.data_pagamento = timezone.now()
                parcela.forma_pagamento = forma
                parcela.observacoes = obs
                parcela.save()

                PagamentoContrato.objects.create(
                    contrato=contrato,
                    parcela=parcela,
                    forma_pagamento=forma,
                    tipo='caucao' if parcela.tipo == 'caucao' else 'locacao',
                    valor=valor,
                    data_pagamento=parcela.data_pagamento,
                    observacoes=obs,
                    registrado_por=request.user,
                )

                # Sincroniza caucao_situacao quando a parcela de caução é paga pela agenda
                if parcela.tipo == 'caucao' and contrato.caucao_situacao == 'pendente':
                    contrato.caucao_situacao = 'pago'
                    contrato.caucao_pago_em = parcela.data_pagamento
                    contrato.save(update_fields=['caucao_situacao', 'caucao_pago_em'])
        except IntegrityError:
            messages.error(
                request,
                'Esta parcela já foi paga por outro lançamento simultâneo. Atualize a página.'
            )
            return redirect('contratos:detalhe', pk=pk)

        messages.success(request, f'Parcela {parcela.numero} registrada como paga — R$ {valor:.2f}.')
        return redirect('contratos:detalhe', pk=pk)


class ContratoAdicionaisView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
    template_name = 'contracts/adicionais.html'

    def get(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk)
        form = AdicionalContratoForm()
        return self._render(request, contrato, form)

    def post(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk)
        form = AdicionalContratoForm(request.POST)
        if form.is_valid():
            adicional = form.save(commit=False)
            adicional.contrato = contrato
            adicional.save()
            messages.success(request, 'Adicional incluído.')
            return redirect('contratos:detalhe', pk=pk)
        return self._render(request, contrato, form)

    def _render(self, request, contrato, form):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'contrato': contrato,
            'adicionais': contrato.adicionais.all(),
            'form': form,
        })


class ContratoAvariasView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
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
            'formas_pagamento': PagamentoContrato.FORMA,
        })


class ContratoAvariaMarcarPagaView(GrupoRequiredMixin, View):
    """Marca uma avaria como paga e registra o PagamentoContrato atomicamente."""
    grupos_permitidos = ['admin_locadora', 'atendente', 'financeiro']

    def post(self, request, pk, avaria_pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk)
        avaria = get_object_or_404(AvariaContrato, pk=avaria_pk, contrato=contrato, situacao='cobrada')

        if not avaria.valor_cobrado:
            messages.error(request, 'Avaria sem valor definido. Defina o valor antes de marcar como paga.')
            return redirect('contratos:avarias', pk=pk)

        forma = request.POST.get('forma_pagamento')
        if not forma:
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


class ContratoPDFView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente', 'financeiro']
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


class ContratoPagamentosView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente', 'financeiro']
    template_name = 'contracts/pagamentos.html'

    def get(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk)
        return self._render(request, contrato)

    def post(self, request, pk):
        from django.db import transaction as db_transaction
        contrato = get_object_or_404(Contrato, pk=pk)
        form = PagamentoContratoForm(request.POST)
        if form.is_valid():
            parcelas_baixadas = 0
            with db_transaction.atomic():
                pagamento = form.save(commit=False)
                pagamento.contrato = contrato
                pagamento.registrado_por = request.user
                pagamento.save()
                # Sincroniza caucao_situacao para liberar o checklist de checkout
                if pagamento.tipo == 'caucao' and contrato.caucao_situacao == 'pendente':
                    contrato.caucao_situacao = 'pago'
                    contrato.caucao_pago_em = pagamento.data_pagamento
                    contrato.save(update_fields=['caucao_situacao', 'caucao_pago_em'])
                # Auto-baixa parcelas de locação FIFO (mais antigas primeiro)
                if pagamento.tipo == 'locacao':
                    saldo_restante = pagamento.valor
                    for parcela in contrato.parcelas.filter(
                        situacao__in=['pendente', 'em_atraso']
                    ).order_by('data_vencimento'):
                        if saldo_restante >= parcela.valor:
                            parcela.situacao = 'pago'
                            parcela.data_pagamento = pagamento.data_pagamento
                            parcela.forma_pagamento = pagamento.forma_pagamento
                            parcela.observacoes = (
                                f'Baixado automaticamente via pagamento de '
                                f'R$ {pagamento.valor:.2f} em '
                                f'{pagamento.data_pagamento.strftime("%d/%m/%Y %H:%M")}'
                            )
                            parcela.save(update_fields=[
                                'situacao', 'data_pagamento', 'forma_pagamento', 'observacoes'
                            ])
                            saldo_restante -= parcela.valor
                            parcelas_baixadas += 1
                        else:
                            break
            msg = f'Pagamento de R$ {pagamento.valor:.2f} registrado com sucesso.'
            if parcelas_baixadas:
                msg += f' {parcelas_baixadas} parcela(s) baixada(s) automaticamente.'
            elif pagamento.tipo == 'locacao':
                restantes = contrato.parcelas.filter(situacao__in=['pendente', 'em_atraso']).count()
                if restantes:
                    msg += ' Valor insuficiente para cobrir a próxima parcela — baixe manualmente na aba de parcelas se necessário.'
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
            'pagamentos': contrato.pagamentos.all(),
        })
