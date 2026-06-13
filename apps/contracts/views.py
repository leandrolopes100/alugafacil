from decimal import Decimal

from django.contrib import messages
from apps.core.mixins import GrupoRequiredMixin
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView, View

from .forms import (ReservaForm, ContratoForm, CheckoutForm, CheckinForm,
                    AdicionalContratoForm, AvariaContratoForm, PagamentoContratoForm)
from .models import (AdicionalContrato, AvariaContrato, Contrato, FotoContrato,
                     PagamentoContrato, ParcelaContrato, Reserva, gerar_parcelas)
from apps.fleet.models import Veiculo


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
        return contexto

    def form_valid(self, form):
        cliente = form.cleaned_data.get('cliente')
        if cliente and cliente.bloqueado:
            form.add_error('cliente', f'Cliente bloqueado: {cliente.motivo_bloqueio or "sem motivo informado"}.')
            return self.form_invalid(form)
        form.instance.criado_por = self.request.user
        messages.success(self.request, 'Contrato criado com sucesso.')
        return super().form_valid(form)


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
        cancelaveis = [p for p in todas_parcelas if p.situacao in ('pendente', 'em_atraso')]
        contexto['parcelas_cancelaveis_qtd'] = len(cancelaveis)
        contexto['parcelas_cancelaveis_valor'] = sum(p.valor for p in cancelaveis)
        # Usa cache de grupos setado pelo GrupoRequiredMixin.dispatch()
        grupos = getattr(self.request.user, '_grupos_nomes_cache', None)
        if grupos is None:
            grupos = set(self.request.user.groups.values_list('name', flat=True))
        contexto['is_admin_locadora'] = 'admin_locadora' in grupos
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

    def _checklist(self, contrato):
        """Retorna lista de (ok: bool, mensagem: str) para cada item do checklist."""
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

        # 4. Seguro do veículo (usa prefetch cache — sem query adicional)
        seguro = next((d for d in docs if d.tipo == 'seguro'), None)
        if seguro and seguro.vencido:
            itens.append((False, f'Seguro vencido em {seguro.data_validade.strftime("%d/%m/%Y")}'))
        elif seguro:
            itens.append((True, f'Seguro em dia até {seguro.data_validade.strftime("%d/%m/%Y")}'))
        else:
            itens.append((None, 'Seguro não cadastrado — verifique a documentação do veículo'))

        # 5. Caução
        if contrato.caucao_valor > 0:
            if contrato.caucao_situacao == 'pendente':
                itens.append((False, f'Caução de R$ {contrato.caucao_valor} não registrado como pago'))
            else:
                itens.append((True, f'Caução de R$ {contrato.caucao_valor} pago'))
        else:
            itens.append((True, 'Sem caução exigido'))

        # 6. Primeira semana paga (upfront conforme modelo de cobrança)
        dias_contrato = max(contrato._dias_previstos(), 1)
        dias_cobranca = min(7, dias_contrato)
        valor_primeira_semana = contrato.diaria * dias_cobranca
        total_locacao_pago = PagamentoContrato.objects.filter(
            contrato=contrato, tipo='locacao'
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0')
        if total_locacao_pago < valor_primeira_semana:
            falta = valor_primeira_semana - total_locacao_pago
            itens.append((False, (
                f'Primeira semana (R$ {valor_primeira_semana:.2f}) não registrada — '
                f'faltam R$ {falta:.2f}. Registre o pagamento antes de liberar o veículo.'
            )))
        else:
            itens.append((True, f'Primeira semana de R$ {valor_primeira_semana:.2f} registrada'))

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

    def get(self, request, pk):
        contrato = self._get_contrato(pk)
        form = CheckoutForm(instance=contrato)
        return self._render(request, contrato, form)

    def post(self, request, pk):
        contrato = self._get_contrato(pk)
        form = CheckoutForm(request.POST, request.FILES, instance=contrato)
        if form.is_valid():
            # Executar checklist — bloqueia somente em False (None = aviso, não bloqueia)
            checklist = self._checklist(contrato)
            bloqueios = [msg for ok, msg in checklist if ok is False]
            if bloqueios:
                for msg in bloqueios:
                    messages.error(request, msg)
                return self._render(request, contrato, form, checklist=checklist)

            from django.db import transaction as db_transaction
            with db_transaction.atomic():
                contrato = form.save(commit=False)
                contrato.situacao = 'ativo'
                contrato.data_saida = timezone.now()
                contrato.save()
                if contrato.km_saida is not None:
                    contrato.veiculo.km_atual = contrato.km_saida
                contrato.veiculo.situacao = 'em_uso'
                contrato.veiculo.save()
                for arquivo in request.FILES.getlist('fotos_saida'):
                    FotoContrato.objects.create(contrato=contrato, momento='saida', imagem=arquivo)
                # Caução: cria apenas se ainda não existe (evita duplicata em re-submit)
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
            checklist = self._checklist(contrato)
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
            messages.success(request, 'Check-in realizado. Veículo aguardando vistoria e encerramento.')
            return redirect('contratos:detalhe', pk=pk)
        return self._render(request, contrato, form)

    def _render(self, request, contrato, form):
        from django.shortcuts import render
        return render(request, self.template_name, {'contrato': contrato, 'form': form})


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
        if saldo > 0:
            partes.append(f'Saldo pendente: R$ {saldo:.2f} — acompanhe em Financeiro → Contas a Receber.')
        if qtd_canceladas > 0:
            partes.append(f'{qtd_canceladas} parcela(s) cancelada(s) da agenda — R$ {valor_cancelado:.2f}.')
        msg = ' '.join(partes)
        if saldo > 0 or qtd_canceladas > 0:
            messages.warning(request, msg)
        else:
            messages.success(request, msg)
        return redirect('contratos:detalhe', pk=pk)


class ContratoReverterCheckinView(GrupoRequiredMixin, View):
    """Desfaz um check-in equivocado, revertendo o contrato de 'aguardando_devolucao'
    para 'ativo'. Restrito a admin_locadora. Exclui fotos de devolução e zera
    todos os campos calculados no fechamento."""
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
        contrato = get_object_or_404(Contrato, pk=pk, situacao='ativo')
        nova_data_str = request.POST.get('nova_data_devolucao')
        try:
            nova_data_naive = parse_datetime(nova_data_str)
            if not nova_data_naive:
                raise ValueError
            nova_data = timezone.make_aware(nova_data_naive)
        except (ValueError, TypeError):
            messages.error(request, 'Data de devolução inválida.')
            return redirect('contratos:detalhe', pk=pk)

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
        with db_transaction.atomic():
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

        # Observação automática que registra multa/juros aplicados para rastreabilidade
        if not obs:
            if valor > parcela.valor:
                multa_aplicada = valor - parcela.valor
                obs = (f'Parcela {parcela.numero} — {parcela.get_tipo_display()} '
                       f'(R$ {parcela.valor:.2f} original + R$ {multa_aplicada:.2f} multa/juros)')
            else:
                obs = f'Parcela {parcela.numero} — {parcela.get_tipo_display()}'

        with db_transaction.atomic():
            parcela.situacao = 'pago'
            parcela.data_pagamento = timezone.now()
            parcela.forma_pagamento = forma
            parcela.observacoes = obs
            parcela.save()

            PagamentoContrato.objects.create(
                contrato=contrato,
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

        with db_transaction.atomic():
            avaria.situacao = 'paga'
            avaria.save(update_fields=['situacao'])
            PagamentoContrato.objects.create(
                contrato=contrato,
                forma_pagamento=forma,
                tipo='avaria',
                valor=avaria.valor_cobrado,
                registrado_por=request.user,
                observacoes=f'Avaria: {avaria.descricao[:100]}',
            )

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
        form = PagamentoContratoForm(initial={'data_pagamento': timezone.now().strftime('%Y-%m-%dT%H:%M')})
        return self._render(request, contrato, form)

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
            return redirect('contratos:pagamentos', pk=pk)
        return self._render(request, contrato, form)

    def _render(self, request, contrato, form):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'contrato': contrato,
            'pagamentos': contrato.pagamentos.all(),
            'form': form,
        })
