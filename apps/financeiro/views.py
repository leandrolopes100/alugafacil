import json
from collections import defaultdict
from decimal import Decimal
from django.contrib import messages
from apps.core.mixins import GrupoRequiredMixin
from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Sum, Value, When
from django.db.models.functions import ExtractMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from datetime import timedelta
from apps.contracts.models import PagamentoContrato, ParcelaContrato

from .forms import (ConfiguracaoLocadoraForm, ContaReceberFiltroForm,
                    DespesaOperacionalForm, MultaTransitoForm, RecebimentoForm)
from .models import (ConfiguracaoLocadora, ContaReceber, DespesaOperacional,
                     MultaTransito, ParcelaDespesa, gerar_parcelas_despesa)


# ─── Configuração da Locadora ────────────────────────────────────────────────

class ConfiguracaoLocadoraView(GrupoRequiredMixin, UpdateView):
    """Permite ao gestor editar as configurações financeiras sem acesso ao admin."""
    grupos_permitidos = ['admin_locadora']
    model = ConfiguracaoLocadora
    form_class = ConfiguracaoLocadoraForm
    template_name = 'financeiro/configuracao.html'

    def get_object(self, queryset=None):
        return ConfiguracaoLocadora.obter()

    def get_success_url(self):
        from django.urls import reverse
        return reverse('financeiro:configuracao')

    def form_valid(self, form):
        messages.success(self.request, 'Configurações financeiras salvas com sucesso.')
        return super().form_valid(form)


# ─── Agenda de Cobranças ─────────────────────────────────────────────────────

class AgendaCobrancasView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'financeiro/agenda.html'

    def get(self, request):
        hoje = timezone.now().date()
        fim_semana = hoje + timedelta(days=7)

        pendentes = list(
            ParcelaContrato.objects.filter(
                situacao__in=['pendente', 'em_atraso']
            ).select_related('contrato__cliente', 'contrato__veiculo')
            .order_by('data_vencimento')
        )

        em_atraso = [p for p in pendentes if p.situacao == 'em_atraso']
        esta_semana = [p for p in pendentes if p.situacao == 'pendente' and p.data_vencimento <= fim_semana]
        proximas = [p for p in pendentes if p.situacao == 'pendente' and p.data_vencimento > fim_semana]

        return render(request, self.template_name, {
            'em_atraso': em_atraso,
            'esta_semana': esta_semana,
            'proximas': proximas,
            'hoje': hoje,
            'formas_pagamento': ParcelaContrato.FORMA,
            'total_atraso': sum(p.valor for p in em_atraso),
            'total_semana': sum(p.valor for p in esta_semana),
        })


# ─── Agenda de Pagamentos (despesas parceladas a pagar) ──────────────────────

class AgendaPagamentosView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'financeiro/agenda_pagamentos.html'

    def get(self, request):
        hoje = timezone.now().date()
        fim_semana = hoje + timedelta(days=7)

        pendentes = list(
            ParcelaDespesa.objects.filter(
                situacao__in=['pendente', 'em_atraso']
            ).select_related('despesa__veiculo')
            .order_by('data_vencimento')
        )

        em_atraso = [p for p in pendentes if p.situacao == 'em_atraso']
        esta_semana = [p for p in pendentes if p.situacao == 'pendente' and p.data_vencimento <= fim_semana]
        proximas = [p for p in pendentes if p.situacao == 'pendente' and p.data_vencimento > fim_semana]

        return render(request, self.template_name, {
            'em_atraso': em_atraso,
            'esta_semana': esta_semana,
            'proximas': proximas,
            'hoje': hoje,
            'total_atraso': sum(p.valor for p in em_atraso),
            'total_semana': sum(p.valor for p in esta_semana),
        })


# ─── Dashboard Financeiro ─────────────────────────────────────────────────────

class IndexFinanceiroView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'financeiro/index.html'

    def get(self, request):
        hoje = timezone.now().date()

        # Totais via aggregate — zero objetos em memória
        resumo = ContaReceber.objects.exclude(
            situacao__in=['pago', 'cancelado']
        ).aggregate(
            total_receber=Sum(F('valor_total') - F('valor_pago')),
            total_vencido=Sum(
                F('valor_total') - F('valor_pago'),
                filter=Q(data_vencimento__lt=hoje)
            ),
        )
        total_receber = resumo['total_receber'] or Decimal('0.00')
        total_vencido = resumo['total_vencido'] or Decimal('0.00')

        # Carrega apenas contas abertas para aging e exibição
        abertas = list(
            ContaReceber.objects.exclude(situacao__in=['pago', 'cancelado'])
            .select_related('cliente', 'contrato__veiculo')
            .order_by('data_vencimento')
        )

        total_recebido_mes = PagamentoContrato.objects.filter(
            data_pagamento__year=hoje.year,
            data_pagamento__month=hoje.month,
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        # Contagens por situação via DB; valores apenas para abertas
        contagens = {
            row['situacao']: row['count']
            for row in ContaReceber.objects.values('situacao').annotate(count=Count('id'))
        }
        por_situacao = {
            s: {'count': contagens.get(s, 0), 'valor': Decimal('0.00')}
            for s, _ in ContaReceber.SITUACAO
        }
        for conta in abertas:
            por_situacao[conta.situacao]['valor'] += conta.valor_saldo

        # Aging por faixa de dias em atraso
        # Chaves sem '+' para serem acessíveis no template Django via dot notation
        aging = {
            'd30':    {'contas': [], 'total': Decimal('0.00'), 'label': '1 – 30 dias'},
            'd60':    {'contas': [], 'total': Decimal('0.00'), 'label': '31 – 60 dias'},
            'd90':    {'contas': [], 'total': Decimal('0.00'), 'label': '61 – 90 dias'},
            'mais90': {'contas': [], 'total': Decimal('0.00'), 'label': 'Mais de 90 dias'},
        }
        for c in abertas:
            if not c.vencida:
                continue
            d = c.dias_em_atraso
            bucket = 'd30' if d <= 30 else 'd60' if d <= 60 else 'd90' if d <= 90 else 'mais90'
            aging[bucket]['contas'].append(c)
            aging[bucket]['total'] += c.valor_saldo

        # Últimas 10 contas abertas ordenadas por vencimento (abertas já ordenado)
        ultimas_abertas = [
            c for c in abertas if c.situacao in ('pendente', 'pago_parcial', 'vencido')
        ][:10]

        # Métricas de contas a pagar (parcelas de despesas)
        total_pagar_mes = ParcelaDespesa.objects.filter(
            situacao__in=['pendente', 'em_atraso'],
            data_vencimento__year=hoje.year,
            data_vencimento__month=hoje.month,
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        # Inclui 'pendente' com data passada: a property em_atraso do model
        # cobre esse caso, mas o campo DB pode ainda estar como 'pendente'.
        total_pagar_vencido = ParcelaDespesa.objects.filter(
            situacao__in=['em_atraso', 'pendente'],
            data_vencimento__lt=hoje,
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        count_pagar_mes = ParcelaDespesa.objects.filter(
            situacao__in=['pendente', 'em_atraso'],
            data_vencimento__year=hoje.year,
            data_vencimento__month=hoje.month,
        ).count()

        proximas_parcelas = list(
            ParcelaDespesa.objects.filter(
                situacao__in=['pendente', 'em_atraso'],
            ).select_related('despesa').order_by('data_vencimento')[:8]
        )

        return render(request, self.template_name, {
            'total_receber': total_receber,
            'total_vencido': total_vencido,
            'total_recebido_mes': total_recebido_mes,
            'por_situacao': por_situacao,
            'aging': aging,
            'ultimas_abertas': ultimas_abertas,
            'hoje': hoje,
            'situacoes_labels': dict(ContaReceber.SITUACAO),
            'total_pagar_mes': total_pagar_mes,
            'total_pagar_vencido': total_pagar_vencido,
            'count_pagar_mes': count_pagar_mes,
            'proximas_parcelas': proximas_parcelas,
        })


# ─── Contas a Receber ─────────────────────────────────────────────────────────

class ContaReceberListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = ContaReceber
    template_name = 'financeiro/contas_receber.html'
    context_object_name = 'contas'
    paginate_by = 25

    def get_queryset(self):
        hoje = timezone.now().date()
        # Ordena por urgência: vencidas abertas (0) → pendentes futuras (1) → pagas/canceladas (2)
        # Dentro de cada grupo, data_vencimento ASC (vencidas mais antigas primeiro).
        qs = ContaReceber.objects.select_related(
            'cliente', 'contrato__veiculo'
        ).annotate(
            prioridade=Case(
                When(situacao__in=['pago', 'cancelado'], then=Value(2)),
                When(data_vencimento__gte=hoje, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        ).order_by('prioridade', 'data_vencimento')

        self.form = ContaReceberFiltroForm(self.request.GET)
        if self.form.is_valid():
            dados = self.form.cleaned_data
            if dados.get('situacao'):
                qs = qs.filter(situacao=dados['situacao'])
            if dados.get('busca'):
                t = dados['busca']
                qs = qs.filter(
                    Q(cliente__nome__icontains=t) |
                    Q(cliente__razao_social__icontains=t) |
                    Q(contrato__numero__icontains=t)
                )
            if dados.get('vencimento_de'):
                qs = qs.filter(data_vencimento__gte=dados['vencimento_de'])
            if dados.get('vencimento_ate'):
                qs = qs.filter(data_vencimento__lte=dados['vencimento_ate'])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        hoje = timezone.now().date()
        base = self.object_list

        # KPI: total faturado
        kpi_faturado = base.aggregate(s=Sum('valor_total'))['s'] or Decimal('0')

        # KPI: total recebido
        kpi_recebido = base.aggregate(s=Sum('valor_pago'))['s'] or Decimal('0')

        # KPI: em aberto (saldo das não-pagas/canceladas)
        agg_aberto = base.exclude(situacao__in=['pago', 'cancelado']).aggregate(
            vt=Sum('valor_total'), vp=Sum('valor_pago')
        )
        kpi_em_aberto = (agg_aberto['vt'] or Decimal('0')) - (agg_aberto['vp'] or Decimal('0'))

        # KPI: vencido (saldo das contas vencidas ainda abertas)
        agg_vencido = base.filter(
            data_vencimento__lt=hoje
        ).exclude(situacao__in=['pago', 'cancelado']).aggregate(
            vt=Sum('valor_total'), vp=Sum('valor_pago')
        )
        kpi_vencido = (agg_vencido['vt'] or Decimal('0')) - (agg_vencido['vp'] or Decimal('0'))

        ctx['form'] = getattr(self, 'form', ContaReceberFiltroForm())
        ctx['total_listado'] = kpi_em_aberto
        ctx['kpi_faturado'] = kpi_faturado
        ctx['kpi_recebido'] = kpi_recebido
        ctx['kpi_em_aberto'] = kpi_em_aberto
        ctx['kpi_vencido'] = kpi_vencido
        ctx['situacoes'] = ContaReceber.SITUACAO
        return ctx


class ContaReceberDetailView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = ContaReceber
    template_name = 'financeiro/conta_receber_detalhe.html'
    context_object_name = 'conta'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['pagamentos'] = self.object.contrato.pagamentos.order_by('-data_pagamento')
        ctx['form'] = RecebimentoForm(initial={
            'data_pagamento': timezone.now().strftime('%Y-%m-%dT%H:%M'),
            'valor': max(self.object.valor_saldo, Decimal('0.00')),
        })
        return ctx


class ReceberPagamentoView(GrupoRequiredMixin, View):
    """Registra um pagamento e atualiza a ContaReceber."""
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        from django.db import transaction
        conta = get_object_or_404(ContaReceber, pk=pk)
        form = RecebimentoForm(request.POST)
        if form.is_valid():
            dados = form.cleaned_data
            parcelas_baixadas = 0
            with transaction.atomic():
                pagamento = PagamentoContrato.objects.create(
                    contrato=conta.contrato,
                    forma_pagamento=dados['forma_pagamento'],
                    tipo=dados['tipo'],
                    valor=dados['valor'],
                    data_pagamento=dados['data_pagamento'],
                    observacoes=dados.get('observacoes', ''),
                    registrado_por=request.user,
                )
                if dados['tipo'] == 'locacao':
                    saldo_restante = dados['valor']
                    for parcela in conta.contrato.parcelas.filter(
                        situacao__in=['pendente', 'em_atraso']
                    ).order_by('data_vencimento'):
                        if saldo_restante >= parcela.valor:
                            parcela.situacao = 'pago'
                            parcela.data_pagamento = dados['data_pagamento']
                            parcela.forma_pagamento = dados['forma_pagamento']
                            parcela.observacoes = (
                                f'Baixado automaticamente via pagamento de '
                                f'R$ {dados["valor"]:.2f} em '
                                f'{dados["data_pagamento"].strftime("%d/%m/%Y %H:%M")}'
                            )
                            parcela.save(update_fields=[
                                'situacao', 'data_pagamento', 'forma_pagamento', 'observacoes'
                            ])
                            saldo_restante -= parcela.valor
                            parcelas_baixadas += 1
                        else:
                            break
            msg = f'Recebimento de R$ {dados["valor"]:.2f} registrado com sucesso.'
            if parcelas_baixadas:
                msg += f' {parcelas_baixadas} parcela(s) baixada(s) automaticamente.'
            elif dados['tipo'] == 'locacao':
                restantes = conta.contrato.parcelas.filter(
                    situacao__in=['pendente', 'em_atraso']
                ).count()
                if restantes:
                    msg += ' Valor insuficiente para cobrir a próxima parcela — baixe manualmente se necessário.'
            messages.success(request, msg)
        else:
            messages.error(request, 'Verifique os campos e tente novamente.')
        return redirect('financeiro:conta-receber-detalhe', pk=pk)


class ContaReceberCancelarView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    def post(self, request, pk):
        conta = get_object_or_404(ContaReceber, pk=pk)
        conta.situacao = 'cancelado'
        conta.save(update_fields=['situacao', 'atualizado_em'])
        messages.success(request, 'Conta cancelada.')
        return redirect('financeiro:contas-receber')


# ─── Despesas ────────────────────────────────────────────────────────────────

class DespesaListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = DespesaOperacional
    template_name = 'financeiro/despesas.html'
    context_object_name = 'despesas'
    paginate_by = 30

    def _mes_filtro(self):
        """Retorna 'YYYY-MM' do filtro ativo. Persiste a seleção na sessão."""
        mes = self.request.GET.get('mes')
        if mes:
            self.request.session['despesa_mes_filtro'] = mes
            return mes
        return self.request.session.get('despesa_mes_filtro') or timezone.now().strftime('%Y-%m')

    def get_queryset(self):
        hoje = timezone.now().date()
        qs = (
            DespesaOperacional.objects
            .select_related('veiculo')
            .prefetch_related('parcelas')
            .annotate(
                tem_atraso=Exists(
                    ParcelaDespesa.objects.filter(
                        despesa=OuterRef('pk'),
                        situacao__in=['pendente', 'em_atraso'],
                        data_vencimento__lt=hoje,
                    )
                ),
                tem_pendente=Exists(
                    ParcelaDespesa.objects.filter(
                        despesa=OuterRef('pk'),
                        situacao__in=['pendente', 'em_atraso'],
                    )
                ),
            )
        )
        categoria = self.request.GET.get('categoria', '')
        busca = self.request.GET.get('busca', '').strip()
        situacao = self.request.GET.get('situacao', '')
        mes = self._mes_filtro()

        if categoria:
            qs = qs.filter(categoria=categoria)
        if busca:
            qs = qs.filter(descricao__icontains=busca)
        try:
            ano, m = mes.split('-')
            qs = qs.filter(data_competencia__year=ano, data_competencia__month=m)
        except (ValueError, AttributeError):
            pass

        if situacao == 'em_atraso':
            qs = qs.filter(tem_atraso=True)
        elif situacao == 'quitado':
            qs = qs.filter(
                Q(parcelado=False, data_pagamento__isnull=False) |
                Q(parcelado=True, tem_pendente=False)
            )
        elif situacao == 'pendente':
            qs = qs.filter(
                Q(parcelado=False, data_pagamento__isnull=True) |
                Q(parcelado=True, tem_pendente=True, tem_atraso=False)
            )

        return qs.order_by('-data_competencia')

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        hoje = timezone.now().date()

        # KPIs sobre toda a lista filtrada (sem paginação)
        base = self.object_list
        pq = ParcelaDespesa.objects.filter(despesa__in=base)
        kpi_pago = (
            (pq.filter(situacao='pago').aggregate(s=Sum('valor'))['s'] or Decimal('0'))
            + (base.filter(parcelado=False, data_pagamento__isnull=False).aggregate(s=Sum('valor'))['s'] or Decimal('0'))
        )
        kpi_em_atraso = (
            pq.filter(
                situacao__in=['pendente', 'em_atraso'],
                data_vencimento__lt=hoje,
            ).aggregate(s=Sum('valor'))['s'] or Decimal('0')
        )
        kpi_pendente = (
            (pq.filter(
                situacao__in=['pendente', 'em_atraso'],
                data_vencimento__gte=hoje,
            ).aggregate(s=Sum('valor'))['s'] or Decimal('0'))
            + (base.filter(parcelado=False, data_pagamento__isnull=True).aggregate(s=Sum('valor'))['s'] or Decimal('0'))
        )

        contexto['form'] = DespesaOperacionalForm(initial={'data_competencia': timezone.now().date()})
        contexto['categorias'] = DespesaOperacional.CATEGORIA
        contexto['mes_filtro'] = self._mes_filtro()
        contexto['total_mes'] = base.aggregate(s=Sum('valor'))['s'] or Decimal('0.00')
        contexto['kpi_pago'] = kpi_pago
        contexto['kpi_em_atraso'] = kpi_em_atraso
        contexto['kpi_pendente'] = kpi_pendente
        contexto['situacao_filtro'] = self.request.GET.get('situacao', '')
        contexto['busca'] = self.request.GET.get('busca', '').strip()
        return contexto


class DespesaCreateView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    def post(self, request):
        form = DespesaOperacionalForm(request.POST)
        if form.is_valid():
            despesa = form.save(commit=False)
            despesa.criado_por = request.user
            if despesa.parcelado:
                despesa.data_pagamento = None
            despesa.save()
            if despesa.parcelado:
                gerar_parcelas_despesa(despesa)
                # Confirma imediatamente parcelas de débito automático já vencidas
                # (cenário: competência no passado ou despesa retroativa)
                if despesa.debito_automatico:
                    DespesaOperacional.sincronizar_auto_pagamento()
                from decimal import ROUND_DOWN
                valor_parcela = (despesa.valor / despesa.numero_parcelas).quantize(
                    Decimal('0.01'), rounding=ROUND_DOWN
                )
                messages.success(
                    request,
                    f'Despesa de R$ {despesa.valor} registrada em {despesa.numero_parcelas}x de '
                    f'R$ {valor_parcela}.'
                )
            else:
                messages.success(request, f'Despesa de R$ {despesa.valor} registrada.')
            mes_salvo = despesa.data_competencia.strftime('%Y-%m')
            from django.urls import reverse
            return redirect(f"{reverse('financeiro:despesas')}?mes={mes_salvo}")

        qs = DespesaOperacional.objects.select_related('veiculo').prefetch_related('parcelas').order_by('-data_competencia')
        categoria = request.GET.get('categoria')
        mes = request.GET.get('mes')
        if categoria:
            qs = qs.filter(categoria=categoria)
        if mes:
            try:
                ano_m, m = mes.split('-')
                qs = qs.filter(data_competencia__year=ano_m, data_competencia__month=m)
            except (ValueError, AttributeError):
                pass

        from django.core.paginator import Paginator
        paginator = Paginator(qs, 30)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        return render(request, 'financeiro/despesas.html', {
            'despesas': page_obj,
            'form': form,
            'categorias': DespesaOperacional.CATEGORIA,
            'total_mes': qs.aggregate(s=Sum('valor'))['s'] or Decimal('0.00'),
            'is_paginated': page_obj.has_other_pages(),
            'page_obj': page_obj,
            'mes_filtro': mes or timezone.now().strftime('%Y-%m'),
        })


class DespesaDetailView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = DespesaOperacional
    template_name = 'financeiro/despesa_detalhe.html'
    context_object_name = 'despesa'

    def get_queryset(self):
        return DespesaOperacional.objects.select_related('veiculo').prefetch_related('parcelas')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        hoje = timezone.now().date()
        ctx['hoje'] = hoje
        if self.object.parcelado:
            # Para cartão de crédito / débito automático: sincroniza parcelas vencidas
            # antes de montar o contexto, refletindo o débito automático na fatura.
            if self.object.debito_automatico or self.object.forma_pagamento == 'cartao_credito':
                ParcelaDespesa.objects.filter(
                    despesa=self.object,
                    situacao__in=['pendente', 'em_atraso'],
                    data_vencimento__lte=hoje,
                ).update(situacao='pago', data_pagamento=hoje, forma_pagamento='cartao_credito')
            parcelas = list(self.object.parcelas.order_by('numero'))
            pagas = sum(1 for p in parcelas if p.situacao == 'pago')
            ctx['parcelas'] = parcelas
            ctx['total_pago'] = sum(p.valor for p in parcelas if p.situacao == 'pago')
            ctx['total_pendente'] = sum(p.valor for p in parcelas if p.situacao != 'pago')
            ctx['pagas'] = pagas
            ctx['restantes'] = len(parcelas) - pagas
        return ctx


class ParcelaDespesaPagarView(GrupoRequiredMixin, View):
    """Marca uma parcela de despesa como paga, registrando a forma de pagamento."""
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        parcela = get_object_or_404(ParcelaDespesa, pk=pk)
        if parcela.situacao != 'pago':
            forma = request.POST.get('forma_pagamento', '')
            parcela.situacao = 'pago'
            parcela.data_pagamento = timezone.now().date()
            parcela.forma_pagamento = forma
            parcela.save(update_fields=['situacao', 'data_pagamento', 'forma_pagamento'])
            messages.success(
                request,
                f'Parcela {parcela.numero}/{parcela.despesa.numero_parcelas} '
                f'(R$ {parcela.valor}) marcada como paga.'
            )
        return redirect('financeiro:despesa-detalhe', pk=parcela.despesa_id)


class ParcelaDespesaEstornarView(GrupoRequiredMixin, View):
    """Estorna o pagamento de uma parcela (volta para pendente)."""
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        parcela = get_object_or_404(ParcelaDespesa, pk=pk)
        if parcela.situacao == 'pago':
            parcela.situacao = 'pendente'
            parcela.data_pagamento = None
            parcela.forma_pagamento = ''
            parcela.save(update_fields=['situacao', 'data_pagamento', 'forma_pagamento'])
            messages.warning(request, f'Pagamento da parcela {parcela.numero} estornado.')
        return redirect('financeiro:despesa-detalhe', pk=parcela.despesa_id)


class ParcelaDespesaEstornarLoteView(GrupoRequiredMixin, View):
    """Estorna o pagamento de múltiplas parcelas de despesa em uma única ação."""
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request):
        from django.http import HttpResponseRedirect
        from django.db import transaction as db_transaction
        ids = request.POST.getlist('parcela_ids')
        next_url = request.POST.get('next', '')

        if not ids:
            messages.warning(request, 'Selecione ao menos uma parcela para estornar.')
            return HttpResponseRedirect(next_url) if next_url else redirect('financeiro:agenda-pagamentos')

        with db_transaction.atomic():
            count = ParcelaDespesa.objects.filter(
                pk__in=ids,
                situacao='pago',
            ).update(situacao='pendente', data_pagamento=None, forma_pagamento='')

        if count:
            s = 's' if count > 1 else ''
            messages.warning(request, f'{count} parcela{s} estornada{s} para pendente.')
        else:
            messages.warning(request, 'Nenhuma parcela elegível para estorno.')

        return HttpResponseRedirect(next_url) if next_url else redirect('financeiro:agenda-pagamentos')


class DespesaDeleteView(GrupoRequiredMixin, View):
    """Exclui uma despesa operacional e suas parcelas (via CASCADE)."""
    grupos_permitidos = ['admin_locadora']

    def post(self, request, pk):
        despesa = get_object_or_404(DespesaOperacional, pk=pk)
        mes = despesa.data_competencia.strftime('%Y-%m')
        desc = despesa.descricao
        despesa.delete()
        messages.success(request, f'Despesa "{desc}" excluída.')
        from django.urls import reverse
        return redirect(f"{reverse('financeiro:despesas')}?mes={mes}")


class DespesaUpdateView(GrupoRequiredMixin, UpdateView):
    """Edição de uma despesa operacional."""
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = DespesaOperacional
    form_class = DespesaOperacionalForm
    template_name = 'financeiro/despesa_form.html'

    def get_success_url(self):
        from django.urls import reverse
        return reverse('financeiro:despesa-detalhe', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.object.debito_automatico or self.object.forma_pagamento == 'cartao_credito':
            DespesaOperacional.sincronizar_auto_pagamento()
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = f'Editar Despesa — {self.object.get_categoria_display()}'
        return ctx

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Despesa atualizada com sucesso.')
        return redirect(self.get_success_url())


class DespesaMarcarPagoView(GrupoRequiredMixin, View):
    """Marca uma despesa simples (não parcelada) como paga."""
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        despesa = get_object_or_404(DespesaOperacional, pk=pk, parcelado=False)
        data_pag = request.POST.get('data_pagamento') or timezone.now().date().isoformat()
        from datetime import date as date_type
        try:
            from datetime import datetime
            despesa.data_pagamento = datetime.strptime(data_pag, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            despesa.data_pagamento = timezone.now().date()
        despesa.save(update_fields=['data_pagamento'])
        messages.success(request, f'Despesa marcada como paga em {despesa.data_pagamento.strftime("%d/%m/%Y")}.')
        return redirect('financeiro:despesa-detalhe', pk=pk)


class DespesaDesmarcarPagoView(GrupoRequiredMixin, View):
    """Remove o pagamento de uma despesa simples (volta para pendente)."""
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        despesa = get_object_or_404(DespesaOperacional, pk=pk, parcelado=False)
        despesa.data_pagamento = None
        despesa.save(update_fields=['data_pagamento'])
        messages.warning(request, 'Pagamento da despesa estornado.')
        return redirect('financeiro:despesa-detalhe', pk=pk)


class ParcelaDespesaPagarLoteView(GrupoRequiredMixin, View):
    """Marca múltiplas parcelas de despesa como pagas em uma única ação."""
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request):
        from django.http import HttpResponseRedirect
        ids = request.POST.getlist('parcela_ids')
        next_url = request.POST.get('next', '')

        if not ids:
            messages.warning(request, 'Selecione ao menos uma parcela.')
            return HttpResponseRedirect(next_url) if next_url else redirect('financeiro:agenda-pagamentos')

        hoje = timezone.now().date()
        from django.db import transaction
        with transaction.atomic():
            count = ParcelaDespesa.objects.filter(
                pk__in=ids,
                situacao__in=['pendente', 'em_atraso'],
            ).update(situacao='pago', data_pagamento=hoje)

        if count:
            s = 's' if count > 1 else ''
            messages.success(request, f'{count} parcela{s} marcada{s} como paga{s}.')
        else:
            messages.warning(request, 'Nenhuma parcela elegível foi encontrada.')

        return HttpResponseRedirect(next_url) if next_url else redirect('financeiro:agenda-pagamentos')


class ContasPagarListView(GrupoRequiredMixin, View):
    """Lista todas as parcelas de despesas pendentes com filtros e pagamento em lote."""
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'financeiro/contas_pagar.html'

    def get(self, request):
        hoje = timezone.now().date()

        qs = ParcelaDespesa.objects.select_related(
            'despesa__veiculo'
        ).order_by('data_vencimento', 'despesa__descricao')

        filtro_situacao = request.GET.get('situacao', '')
        filtro_mes = request.GET.get('mes', '')
        filtro_categoria = request.GET.get('categoria', '')

        if filtro_situacao:
            qs = qs.filter(situacao=filtro_situacao)
        else:
            qs = qs.filter(situacao__in=['pendente', 'em_atraso'])

        if filtro_mes:
            try:
                ano, m = filtro_mes.split('-')
                qs = qs.filter(data_vencimento__year=int(ano), data_vencimento__month=int(m))
            except (ValueError, AttributeError):
                pass

        if filtro_categoria:
            qs = qs.filter(despesa__categoria=filtro_categoria)

        totais = qs.aggregate(
            total_listado=Sum('valor'),
            total_vencido=Sum('valor', filter=Q(situacao='em_atraso')),
        )
        total_listado = totais['total_listado'] or Decimal('0.00')
        total_vencido = totais['total_vencido'] or Decimal('0.00')
        parcelas = list(qs)
        total_pendente_mes = ParcelaDespesa.objects.filter(
            situacao__in=['pendente', 'em_atraso'],
            data_vencimento__year=hoje.year,
            data_vencimento__month=hoje.month,
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        return render(request, self.template_name, {
            'parcelas': parcelas,
            'total_listado': total_listado,
            'total_vencido': total_vencido,
            'total_pendente_mes': total_pendente_mes,
            'hoje': hoje,
            'categorias': DespesaOperacional.CATEGORIA,
            'filtro_situacao': filtro_situacao,
            'filtro_mes': filtro_mes or hoje.strftime('%Y-%m'),
            'filtro_categoria': filtro_categoria,
            'situacoes': [
                ('', 'Pendentes + Em Atraso'),
                ('pendente', 'Pendente'),
                ('em_atraso', 'Em Atraso'),
                ('pago', 'Pago'),
            ],
        })


# ─── Fluxo de Caixa ──────────────────────────────────────────────────────────

class FluxoCaixaView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'financeiro/fluxo.html'

    def get(self, request):
        hoje = timezone.now()
        ano = int(request.GET.get('ano', hoje.year))
        mes = int(request.GET.get('mes', hoje.month))

        pagamentos = PagamentoContrato.objects.filter(
            data_pagamento__year=ano, data_pagamento__month=mes
        ).select_related('contrato__cliente', 'contrato__veiculo')

        total_entradas = pagamentos.aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        # Agrega por forma e converte para label legível em Python
        _entradas_raw = pagamentos.values('forma_pagamento').annotate(
            total=Sum('valor')
        ).order_by('-total')
        _forma_labels = dict(PagamentoContrato.FORMA)
        entradas_por_forma = [
            {'forma_pagamento': _forma_labels.get(item['forma_pagamento'], item['forma_pagamento']),
             'total': item['total']}
            for item in _entradas_raw
        ]

        # Despesas NÃO parceladas: competência no mês
        despesas_simples = list(DespesaOperacional.objects.filter(
            parcelado=False,
            data_competencia__year=ano,
            data_competencia__month=mes,
        ).select_related('veiculo'))

        # Parcelas de despesas parceladas: vencimento no mês
        parcelas_mes = list(ParcelaDespesa.objects.filter(
            data_vencimento__year=ano,
            data_vencimento__month=mes,
        ).select_related('despesa__veiculo'))

        total_simples = sum(d.valor for d in despesas_simples)
        total_parcelas = sum(p.valor for p in parcelas_mes)
        total_saidas = total_simples + total_parcelas

        # Saídas por categoria (unificado em Python)
        por_cat = defaultdict(Decimal)
        for d in despesas_simples:
            por_cat[d.get_categoria_display()] += d.valor
        for p in parcelas_mes:
            por_cat[p.despesa.get_categoria_display()] += p.valor
        saidas_por_categoria = sorted(
            [{'categoria': k, 'total': v} for k, v in por_cat.items()],
            key=lambda x: -x['total'],
        )

        # Lista detalhada unificada para o template
        saidas_detalhadas = []
        for d in despesas_simples:
            saidas_detalhadas.append({
                'data': d.data_competencia,
                'descricao': d.descricao,
                'valor': d.valor,
                'parcela_info': None,
                'despesa_pk': d.pk,
                'situacao': 'pago' if d.data_pagamento else 'pendente',
                'debito_automatico': False,
            })
        for p in parcelas_mes:
            saidas_detalhadas.append({
                'data': p.data_vencimento,
                'descricao': p.despesa.descricao,
                'valor': p.valor,
                'parcela_info': f'{p.numero}/{p.despesa.numero_parcelas}',
                'despesa_pk': p.despesa_id,
                'situacao': p.situacao,
                'debito_automatico': p.despesa.debito_automatico,
            })
        saidas_detalhadas.sort(key=lambda x: x['data'])

        saldo = total_entradas - total_saidas

        _nomes = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
        _abrev = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
                  'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        meses = [(m, _abrev[m]) for m in range(1, 13)]

        # Dados anuais para o gráfico de barras (3 queries extras)
        _ent_ano = {
            item['m']: float(item['total'])
            for item in PagamentoContrato.objects.filter(
                data_pagamento__year=ano
            ).annotate(m=ExtractMonth('data_pagamento')).values('m').annotate(total=Sum('valor'))
        }
        _sai_sim_ano = {
            item['m']: float(item['total'])
            for item in DespesaOperacional.objects.filter(
                parcelado=False, data_competencia__year=ano
            ).annotate(m=ExtractMonth('data_competencia')).values('m').annotate(total=Sum('valor'))
        }
        _sai_par_ano = {
            item['m']: float(item['total'])
            for item in ParcelaDespesa.objects.filter(
                data_vencimento__year=ano
            ).annotate(m=ExtractMonth('data_vencimento')).values('m').annotate(total=Sum('valor'))
        }
        grafico_entradas = [_ent_ano.get(m, 0) for m in range(1, 13)]
        grafico_saidas = [round(_sai_sim_ano.get(m, 0) + _sai_par_ano.get(m, 0), 2) for m in range(1, 13)]
        grafico_saldo = [round(grafico_entradas[i] - grafico_saidas[i], 2) for i in range(12)]

        return render(request, self.template_name, {
            'ano': ano,
            'mes': mes,
            'mes_nome': _nomes[mes],
            'pagamentos': pagamentos,
            'total_entradas': total_entradas,
            'entradas_por_forma': entradas_por_forma,
            'saidas_detalhadas': saidas_detalhadas,
            'total_saidas': total_saidas,
            'saidas_por_categoria': saidas_por_categoria,
            'saldo': saldo,
            'meses': meses,
            'grafico_labels': json.dumps(_abrev[1:]),
            'grafico_entradas': json.dumps(grafico_entradas),
            'grafico_saidas': json.dumps(grafico_saidas),
            'grafico_saldo': json.dumps(grafico_saldo),
        })


# ─── Multas ──────────────────────────────────────────────────────────────────

class MultaListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = MultaTransito
    template_name = 'financeiro/multas.html'
    context_object_name = 'multas'
    paginate_by = 20

    def get_queryset(self):
        qs = MultaTransito.objects.select_related('veiculo', 'contrato__cliente')
        situacao = self.request.GET.get('situacao')
        if situacao:
            qs = qs.filter(situacao=situacao)
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['situacoes'] = MultaTransito.SITUACAO
        contexto['filtro_situacao'] = self.request.GET.get('situacao', '')
        contexto['pendentes_criticas'] = MultaTransito.objects.filter(
            situacao='pendente_identificacao'
        ).count()
        return contexto


class MultaCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = MultaTransito
    form_class = MultaTransitoForm
    template_name = 'financeiro/multa_form.html'

    def get_success_url(self):
        from django.urls import reverse
        return reverse('financeiro:multa-detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Registrar Multa'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, 'Multa registrada com sucesso.')
        return super().form_valid(form)


class MultaDetailView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = MultaTransito
    template_name = 'financeiro/multa_detalhe.html'
    context_object_name = 'multa'


class MultaUpdateView(GrupoRequiredMixin, UpdateView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = MultaTransito
    form_class = MultaTransitoForm
    template_name = 'financeiro/multa_form.html'

    def get_success_url(self):
        from django.urls import reverse
        return reverse('financeiro:multa-detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = f'Editar Multa — {self.object.veiculo.placa}'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, 'Multa atualizada.')
        return super().form_valid(form)
