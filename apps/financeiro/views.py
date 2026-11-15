import json
from collections import defaultdict
from decimal import Decimal
from django.contrib import messages
from apps.core.mixins import GrupoRequiredMixin
from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, Sum, Value, When
from django.db.models.functions import ExtractMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from datetime import timedelta
from apps.contracts.models import PagamentoContrato, ParcelaContrato

from .forms import (ConfiguracaoLocadoraForm, ContaReceberFiltroForm,
                    DespesaOperacionalForm, MultaTransitoForm, RecebimentoForm)
from .models import (ConfiguracaoLocadora, ContaReceber, DespesaOperacional,
                     MultaTransito, NotificacaoCobranca, ParcelaDespesa,
                     cobrancas_gestao_consideradas, gerar_parcelas_despesa, resumo_contas_a_pagar)


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

    def get_context_data(self, **kwargs):
        from .pix import qr_code_svg
        contexto = super().get_context_data(**kwargs)
        config = ConfiguracaoLocadora.obter()
        pix_teste = config.gerar_pix(Decimal('0.01'), 'TESTEALUGAFACIL')
        if pix_teste:
            contexto['pix_teste'] = pix_teste
            contexto['pix_teste_qr'] = qr_code_svg(pix_teste)
        return contexto

    def form_valid(self, form):
        messages.success(self.request, 'Configurações financeiras salvas com sucesso.')
        return super().form_valid(form)


# ─── Cobrança pública (link enviado ao motorista) ────────────────────────────

class CobrancaPublicaView(View):
    template_name = 'financeiro/cobranca_publica.html'

    def get(self, request, token):
        from django.core import signing
        from apps.core.utils import numero_whatsapp
        from .pix import qr_code_svg

        config = ConfiguracaoLocadora.obter()
        contexto = {
            'config': config,
            'whatsapp_locadora': numero_whatsapp(config.whatsapp_locadora),
        }
        status = 200
        try:
            parcela = ParcelaContrato.obter_por_token_cobranca(token)
        except signing.SignatureExpired:
            contexto['link_invalido'] = 'Este link de pagamento expirou.'
            status = 410
        except (signing.BadSignature, ParcelaContrato.DoesNotExist):
            contexto['link_invalido'] = 'Este link de pagamento não é válido.'
            status = 404
        else:
            pix = parcela.pix_copia_e_cola(config)
            contexto.update({
                'parcela': parcela,
                'primeiro_nome': parcela.contrato.cliente.nome_exibicao.split(' ')[0],
                'pix': pix,
                'pix_qr': qr_code_svg(pix) if pix else None,
                'mensagem_comprovante': (
                    f'Olá! Segue o comprovante da parcela {parcela.numero} do contrato '
                    f'{parcela.contrato.numero} (placa {parcela.contrato.veiculo.placa}).'
                ),
            })

        resposta = render(request, self.template_name, contexto, status=status)
        resposta['Cache-Control'] = 'no-store'
        resposta['X-Robots-Tag'] = 'noindex, nofollow'
        resposta['Referrer-Policy'] = 'no-referrer'
        return resposta


# ─── Agenda de Cobranças ─────────────────────────────────────────────────────

class AgendaCobrancasView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'financeiro/agenda.html'

    def get(self, request):
        hoje = timezone.localdate()
        fim_semana = hoje + timedelta(days=7)

        pendentes = list(
            ParcelaContrato.objects.filter(
                situacao__in=['pendente', 'em_atraso']
            ).select_related('contrato__cliente', 'contrato__veiculo')
            .order_by('data_vencimento')
        )

        em_atraso = [p for p in pendentes if p.em_atraso]
        esta_semana = [p for p in pendentes if not p.em_atraso and p.data_vencimento <= fim_semana]
        proximas = [p for p in pendentes if not p.em_atraso and p.data_vencimento > fim_semana]

        from apps.contracts.models import AvariaContrato
        avarias_a_cobrar = list(
            AvariaContrato.objects.filter(situacao='cobrada', valor_cobrado__gt=0)
            .exclude(contrato__situacao='cancelado')
            .select_related('contrato__cliente', 'contrato__veiculo')
            .order_by('criado_em')
        )

        return render(request, self.template_name, {
            'avarias_a_cobrar': avarias_a_cobrar,
            'a_contatar': self._a_contatar(request, pendentes, hoje),
            'em_atraso': em_atraso,
            'esta_semana': esta_semana,
            'proximas': proximas,
            'hoje': hoje,
            'formas_pagamento': ParcelaContrato.FORMAS_RECEBIMENTO,
            'total_atraso': sum(p.valor_corrigido for p in em_atraso),
            'total_semana': sum(p.valor_corrigido for p in esta_semana),
        })


    def _a_contatar(self, request, pendentes, hoje):
        from urllib.parse import quote
        config = ConfiguracaoLocadora.obter()
        itens = []
        for parcela, etapa, enviada in NotificacaoCobranca.a_contatar('whatsapp', pendentes, hoje):
            numero = parcela.contrato.cliente.whatsapp
            link_whatsapp = None
            if numero:
                mensagem = NotificacaoCobranca.mensagem(
                    parcela, etapa,
                    request.build_absolute_uri(parcela.caminho_cobranca),
                    parcela.pix_copia_e_cola(config),
                )
                link_whatsapp = f'https://wa.me/{numero}?text={quote(mensagem)}'
            itens.append({
                'parcela': parcela,
                'etapa': etapa,
                'etapa_rotulo': dict(NotificacaoCobranca.ETAPA)[etapa],
                'enviada': enviada,
                'link_whatsapp': link_whatsapp,
            })
        return itens


class RegistrarCobrancaWhatsappView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        parcela = get_object_or_404(
            ParcelaContrato.objects.select_related('contrato__cliente'),
            pk=pk, situacao__in=['pendente', 'em_atraso'],
        )
        etapa = NotificacaoCobranca.etapa_do_dia(parcela.data_vencimento)
        if not etapa:
            return JsonResponse({'erro': 'Parcela fora da régua de cobrança.'}, status=400)
        notificacao, _ = NotificacaoCobranca.objects.get_or_create(
            parcela=parcela, canal='whatsapp', etapa=etapa,
            defaults={
                'destino': parcela.contrato.cliente.whatsapp or '',
                'valor': parcela.valor_corrigido,
                'enviado_por': request.user,
            },
        )
        return JsonResponse({
            'etapa': notificacao.get_etapa_display(),
            'enviado_em': timezone.localtime(notificacao.enviado_em).strftime('%H:%M'),
        })


# ─── Agenda de Pagamentos (despesas parceladas a pagar) ──────────────────────

class AgendaPagamentosView(GrupoRequiredMixin, View):
    """Mantido para compatibilidade — redireciona para Contas a Pagar."""
    grupos_permitidos = ['admin_locadora', 'financeiro']
    def get(self, request):
        return redirect('financeiro:contas-pagar')


# ─── Dashboard Financeiro ─────────────────────────────────────────────────────

class IndexFinanceiroView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'financeiro/index.html'

    def get(self, request):
        hoje = timezone.localdate()

        resumo = ContaReceber.resumo_aberto(hoje)
        abertas = resumo['abertas']
        total_receber = resumo['total_aberto']
        total_vencido = resumo['total_vencido']

        total_recebido_mes = PagamentoContrato.objects.receita().filter(
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
        for c in resumo['em_atraso']:
            d = c.dias_atraso_efetivo
            bucket = 'd30' if d <= 30 else 'd60' if d <= 60 else 'd90' if d <= 90 else 'mais90'
            aging[bucket]['contas'].append(c)
            aging[bucket]['total'] += c.valor_vencido

        # Últimas 10 contas abertas ordenadas por vencimento (abertas já ordenado)
        ultimas_abertas = [
            c for c in abertas if c.situacao in ('pendente', 'pago_parcial', 'vencido')
        ][:10]

        a_pagar = resumo_contas_a_pagar(hoje)
        total_pagar_mes = a_pagar['total_mes']
        total_pagar_vencido = a_pagar['total_vencido']

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
        hoje = timezone.localdate()
        qs = ContaReceber.objects.select_related(
            'cliente', 'contrato__veiculo'
        ).com_parcelas_atrasadas(hoje).annotate(
            prioridade=Case(
                When(situacao__in=['pago', 'cancelado'], then=Value(2)),
                When(Q(data_vencimento__lt=hoje) | Q(qtd_parcelas_atrasadas__gt=0), then=Value(0)),
                default=Value(1),
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

        abertas_com_atraso = ContaReceber.anexar_encargos(
            c for c in base.exclude(situacao__in=['pago', 'cancelado']) if c.tem_atraso
        )
        kpi_vencido = sum((c.valor_vencido for c in abertas_com_atraso), Decimal('0'))
        ctx['contas'] = ContaReceber.anexar_encargos(ctx['contas'])
        ctx['kpi_vencido_atualizado'] = sum((c.valor_vencido_atualizado for c in abertas_com_atraso), Decimal('0'))

        ctx['form'] = getattr(self, 'form', ContaReceberFiltroForm())
        ctx['total_listado'] = kpi_em_aberto
        ctx['kpi_faturado'] = kpi_faturado
        ctx['kpi_recebido'] = kpi_recebido
        ctx['kpi_em_aberto'] = kpi_em_aberto
        ctx['kpi_vencido'] = kpi_vencido
        ctx['qtd_contas_em_atraso'] = len(abertas_com_atraso)
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
            'valor': self.object.valor_atualizado,
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
            from apps.contracts.models import Contrato
            with transaction.atomic():
                contrato = Contrato.objects.select_for_update().get(pk=conta.contrato_id)
                parcelas_baixadas = contrato.registrar_recebimento(
                    dados['tipo'], dados['valor'], dados['forma_pagamento'], dados['data_pagamento'],
                    request.user, dados.get('observacoes', ''),
                )
            msg = f'Recebimento de R$ {dados["valor"]:.2f} registrado com sucesso.'
            if parcelas_baixadas:
                msg += f' Aplicado em {parcelas_baixadas} parcela(s), da mais antiga para a mais nova.'
            messages.success(request, msg)
        else:
            messages.error(request, 'Verifique os campos e tente novamente.')
        return redirect('financeiro:conta-receber-detalhe', pk=pk)


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
        hoje = timezone.localdate()
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
        hoje = timezone.localdate()

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

        from datetime import date as _date
        from django.utils.formats import date_format
        mes_filtro = self._mes_filtro()
        try:
            _ano, _m = mes_filtro.split('-')
            mes_display = date_format(_date(int(_ano), int(_m), 1), 'F Y')
        except Exception:
            mes_display = mes_filtro

        contexto['form'] = DespesaOperacionalForm(initial={'data_competencia': timezone.localdate()})
        contexto['categorias'] = DespesaOperacional.CATEGORIA
        contexto['mes_filtro'] = mes_filtro
        contexto['mes_display'] = mes_display
        contexto['total_mes'] = kpi_pago + kpi_pendente + kpi_em_atraso
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
        hoje = timezone.localdate()
        ctx['hoje'] = hoje
        if self.object.parcelado:
            # Para cartão de crédito / débito automático: sincroniza parcelas vencidas
            # antes de montar o contexto, refletindo o débito automático na fatura.
            if self.object.debito_automatico:
                DespesaOperacional.sincronizar_auto_pagamento()
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
            parcela.data_pagamento = timezone.localdate()
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = f'Editar Despesa — {self.object.get_categoria_display()}'
        return ctx

    def form_valid(self, form):
        self.object = form.save()
        if self.object.debito_automatico:
            DespesaOperacional.sincronizar_auto_pagamento()
        messages.success(self.request, 'Despesa atualizada com sucesso.')
        return redirect(self.get_success_url())


class DespesaMarcarPagoView(GrupoRequiredMixin, View):
    """Marca uma despesa simples (não parcelada) como paga."""
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        despesa = get_object_or_404(DespesaOperacional, pk=pk, parcelado=False)
        data_pag = request.POST.get('data_pagamento') or timezone.localdate().isoformat()
        from datetime import date as date_type
        try:
            from datetime import datetime
            despesa.data_pagamento = datetime.strptime(data_pag, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            despesa.data_pagamento = timezone.localdate()
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

        hoje = timezone.localdate()
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

        return HttpResponseRedirect(next_url) if next_url else redirect('financeiro:contas-pagar')


class ContasPagarListView(GrupoRequiredMixin, View):
    """Parcelas de despesas: modo urgência (grupos temporais) + lista filtrada."""
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'financeiro/contas_pagar.html'

    def get(self, request):
        hoje = timezone.localdate()
        fim_semana = hoje + timedelta(days=7)

        # ── Grupos de urgência — todas as pendentes, sem filtro de mês ──
        todas_pendentes = list(
            ParcelaDespesa.objects.filter(
                situacao__in=['pendente', 'em_atraso']
            ).select_related('despesa__veiculo')
            .order_by('data_vencimento', 'despesa__descricao')
        )
        em_atraso_grupo = [
            p for p in todas_pendentes
            if p.situacao == 'em_atraso' or (p.situacao == 'pendente' and p.data_vencimento < hoje)
        ]
        esta_semana_grupo = [
            p for p in todas_pendentes
            if p.situacao == 'pendente' and hoje <= p.data_vencimento <= fim_semana
        ]
        proximas_grupo = [
            p for p in todas_pendentes
            if p.situacao == 'pendente' and p.data_vencimento > fim_semana
        ]

        # ── Lista filtrada ──
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

        parcelas = list(qs)
        total_listado = sum(p.valor for p in parcelas)

        # ── KPIs globais ──
        a_pagar = resumo_contas_a_pagar(hoje)
        kpi_em_atraso = a_pagar['total_vencido']
        kpi_este_mes = a_pagar['total_mes']
        kpi_total_pendente = a_pagar['total_pendente']

        # Modo inicial: lista quando filtros ativos; urgência por padrão
        tem_filtro = bool(
            filtro_situacao or filtro_categoria
            or (filtro_mes and filtro_mes != hoje.strftime('%Y-%m'))
        )

        return render(request, self.template_name, {
            # Urgência
            'em_atraso_grupo': em_atraso_grupo,
            'esta_semana_grupo': esta_semana_grupo,
            'proximas_grupo': proximas_grupo,
            'total_atraso': sum(p.valor for p in em_atraso_grupo),
            'avulsas_pendentes': a_pagar['avulsas'],
            'repasses_pendentes': a_pagar['repasses'],
            'total_semana': sum(p.valor for p in esta_semana_grupo),
            # Lista
            'parcelas': parcelas,
            'total_listado': total_listado,
            # KPIs
            'kpi_em_atraso': kpi_em_atraso,
            'kpi_este_mes': kpi_este_mes,
            'kpi_total_pendente': kpi_total_pendente,
            # Contexto
            'hoje': hoje,
            'categorias': DespesaOperacional.CATEGORIA,
            'filtro_situacao': filtro_situacao,
            'filtro_mes': filtro_mes or hoje.strftime('%Y-%m'),
            'filtro_categoria': filtro_categoria,
            'modo_inicial': 'lista' if tem_filtro else 'urgencia',
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
        hoje = timezone.localdate()
        from apps.core.utils import ler_ano_mes
        ano, mes = ler_ano_mes(request.GET, hoje, hoje.month)
        mes = mes or hoje.month

        pagamentos = PagamentoContrato.objects.entradas_de_caixa().filter(
            data_pagamento__year=ano, data_pagamento__month=mes
        ).select_related('contrato__cliente', 'contrato__veiculo')

        total_entradas = pagamentos.aggregate(s=Sum('valor'))['s'] or Decimal('0.00')
        total_regularizacao = pagamentos.filter(tipo='regularizacao').aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        _entradas_raw = pagamentos.exclude(tipo='regularizacao').values('forma_pagamento').annotate(
            total=Sum('valor')
        ).order_by('-total')
        _forma_labels = dict(PagamentoContrato.FORMA)
        entradas_por_forma = [
            {'forma_pagamento': _forma_labels.get(item['forma_pagamento'], item['forma_pagamento']),
             'total': item['total']}
            for item in _entradas_raw
        ]

        repasses = cobrancas_gestao_consideradas()
        despesas_simples = list(DespesaOperacional.objects.filter(
            parcelado=False,
        ).filter(
            Q(data_pagamento__year=ano, data_pagamento__month=mes)
            | Q(data_pagamento__isnull=True, data_competencia__year=ano, data_competencia__month=mes)
        ).select_related('veiculo'))
        parcelas_mes = list(ParcelaDespesa.objects.filter(
            Q(situacao='pago', data_pagamento__year=ano, data_pagamento__month=mes)
            | Q(~Q(situacao='pago'), data_vencimento__year=ano, data_vencimento__month=mes)
        ).select_related('despesa__veiculo'))
        repasses_mes = list(repasses.filter(
            Q(situacao='pago', data_pagamento__year=ano, data_pagamento__month=mes)
            | Q(situacao='pendente', data_vencimento__year=ano, data_vencimento__month=mes)
        ).select_related('veiculo_investidor__investidor', 'veiculo_investidor__veiculo'))

        saidas_detalhadas = []
        for d in despesas_simples:
            saidas_detalhadas.append({
                'data': d.data_pagamento or d.data_competencia,
                'descricao': d.descricao,
                'categoria': d.get_categoria_display(),
                'valor': d.valor,
                'parcela_info': None,
                'despesa_pk': d.pk,
                'situacao': 'pago' if d.data_pagamento else ('em_atraso' if d.data_competencia < hoje else 'pendente'),
                'debito_automatico': False,
            })
        for p in parcelas_mes:
            saidas_detalhadas.append({
                'data': p.data_pagamento if p.situacao == 'pago' else p.data_vencimento,
                'descricao': p.despesa.descricao,
                'categoria': p.despesa.get_categoria_display(),
                'valor': p.valor,
                'parcela_info': f'{p.numero}/{p.despesa.numero_parcelas}',
                'despesa_pk': p.despesa_id,
                'situacao': 'em_atraso' if p.em_atraso else p.situacao,
                'debito_automatico': p.despesa.debito_automatico,
            })
        for r in repasses_mes:
            saidas_detalhadas.append({
                'data': r.data_pagamento if r.situacao == 'pago' else r.data_vencimento,
                'descricao': f'Repasse a investidor — {r.veiculo_investidor.investidor.nome_exibicao} ({r.veiculo_investidor.veiculo.placa})',
                'categoria': 'Repasse a investidores',
                'valor': r.valor,
                'parcela_info': None,
                'despesa_pk': None,
                'situacao': 'pago' if r.situacao == 'pago' else ('em_atraso' if r.em_atraso else 'pendente'),
                'debito_automatico': False,
            })
        saidas_detalhadas.sort(key=lambda x: x['data'])

        pagas = [i for i in saidas_detalhadas if i['situacao'] == 'pago']
        total_saidas = sum((i['valor'] for i in pagas), Decimal('0.00'))
        total_a_pagar = sum(
            (i['valor'] for i in saidas_detalhadas if i['situacao'] != 'pago'), Decimal('0.00')
        )
        por_cat = defaultdict(Decimal)
        for i in pagas:
            por_cat[i['categoria']] += i['valor']
        saidas_por_categoria = sorted(
            [{'categoria': k, 'total': v} for k, v in por_cat.items()],
            key=lambda x: -x['total'],
        )

        saldo = total_entradas - total_saidas

        _nomes = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
        _abrev = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
                  'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        meses = [(m, _abrev[m]) for m in range(1, 13)]

        # Dados anuais para o gráfico de barras (3 queries extras)
        _ent_ano = {
            item['m']: float(item['total'])
            for item in PagamentoContrato.objects.entradas_de_caixa().filter(
                data_pagamento__year=ano
            ).annotate(m=ExtractMonth('data_pagamento')).values('m').annotate(total=Sum('valor'))
        }
        _sai_ano = defaultdict(float)
        for modelo, filtro in (
            (DespesaOperacional.objects, dict(parcelado=False, data_pagamento__year=ano)),
            (ParcelaDespesa.objects, dict(situacao='pago', data_pagamento__year=ano)),
            (repasses, dict(situacao='pago', data_pagamento__year=ano)),
        ):
            for item in modelo.filter(**filtro).annotate(
                m=ExtractMonth('data_pagamento')
            ).values('m').annotate(total=Sum('valor')):
                _sai_ano[item['m']] += float(item['total'])
        grafico_entradas = [_ent_ano.get(m, 0) for m in range(1, 13)]
        grafico_saidas = [round(_sai_ano.get(m, 0), 2) for m in range(1, 13)]
        grafico_saldo = [round(grafico_entradas[i] - grafico_saidas[i], 2) for i in range(12)]

        return render(request, self.template_name, {
            'ano': ano,
            'mes': mes,
            'mes_nome': _nomes[mes],
            'pagamentos': pagamentos,
            'total_entradas': total_entradas,
            'total_regularizacao': total_regularizacao,
            'total_entradas_do_mes': total_entradas - total_regularizacao,
            'entradas_por_forma': entradas_por_forma,
            'saidas_detalhadas': saidas_detalhadas,
            'total_saidas': total_saidas,
            'total_a_pagar': total_a_pagar,
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


class MultaAlterarSituacaoView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        from django.db import transaction
        with transaction.atomic():
            multa = get_object_or_404(MultaTransito.objects.select_for_update(), pk=pk)
            nova = request.POST.get('situacao', '')
            erro = multa.erro_ao_mudar_para(nova)
            if erro:
                messages.error(request, erro)
                return redirect('financeiro:multa-detalhe', pk=pk)
            anterior = multa.parcela_id
            multa.situacao = nova
            multa.save()
        msg = f'Situação alterada para "{multa.get_situacao_display()}".'
        if nova == 'cobrada_cliente' and multa.parcela and not anterior:
            msg += (
                f' Cobrança de R$ {multa.parcela.valor:.2f} criada no contrato {multa.contrato.numero}, '
                f'com vencimento em {multa.parcela.data_vencimento:%d/%m/%Y}.'
            )
        elif nova == 'cobrada_cliente' and multa.parcela:
            msg += f' Cobrança no contrato {multa.contrato.numero} reaberta.'
        elif nova == 'contestada' and multa.parcela and multa.parcela.situacao == 'cancelada':
            msg += ' A cobrança no contrato foi cancelada enquanto a multa estiver em contestação.'
        messages.success(request, msg)
        return redirect('financeiro:multa-detalhe', pk=pk)


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
