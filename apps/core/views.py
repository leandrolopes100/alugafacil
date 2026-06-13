import calendar
import csv
import json
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from apps.core.mixins import GrupoRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.contracts.models import Contrato, PagamentoContrato, ParcelaContrato
from apps.customers.models import Cliente
from apps.financeiro.models import ContaReceber, DespesaOperacional, MultaTransito
from apps.fleet.models import Veiculo


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _periodo(request, modo='mes'):
    hoje = timezone.now()
    ano = int(request.GET.get('ano', hoje.year))
    mes_raw = request.GET.get('mes', '' if modo == 'ano' else str(hoje.month))
    mes = int(mes_raw) if mes_raw else 0
    anos_disp = list(range(max(hoje.year - 4, 2020), hoje.year + 1))
    meses_disp = [(m, calendar.month_name[m]) for m in range(1, 13)]
    return ano, mes, {
        'ano_sel': ano,
        'mes_sel': mes,
        'anos_disp': anos_disp,
        'meses_disp': meses_disp,
        'mes_nome': calendar.month_name[mes] if mes else 'Ano completo',
    }


# ─── Dashboard ────────────────────────────────────────────────────────────────

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'core/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        hoje = timezone.now().date()

        # Contagens de veiculos em uma unica query
        situacoes = Veiculo.objects.values('situacao').annotate(n=Count('id'))
        sit_map = {s['situacao']: s['n'] for s in situacoes}
        ctx['veiculos_disponiveis'] = sit_map.get('disponivel', 0)
        ctx['veiculos_em_uso']      = sit_map.get('em_uso', 0)
        ctx['veiculos_manutencao']  = sit_map.get('manutencao', 0)
        ctx['total_veiculos']       = sum(v for k, v in sit_map.items() if k != 'inativo')

        # Bug 7 fix: list() avalia uma única query; evita COUNT + SELECT separados
        abertos = list(
            Contrato.objects.filter(
                situacao__in=['ativo', 'aguardando_devolucao']
            ).select_related('cliente', 'veiculo')
        )
        ctx['contratos_ativos'] = len(abertos)
        ctx['contratos_atraso'] = [c for c in abertos if c.em_atraso]

        ctx['devolucoes_hoje'] = Contrato.objects.filter(
            situacao='ativo', data_devolucao_prevista__date=hoje,
        ).select_related('cliente', 'veiculo')

        ctx['ultimos_contratos'] = Contrato.objects.select_related(
            'cliente', 'veiculo'
        ).order_by('-criado_em')[:5]

        ctx['total_clientes'] = Cliente.objects.count()

        ctx['receita_mes'] = PagamentoContrato.objects.filter(
            data_pagamento__year=hoje.year,
            data_pagamento__month=hoje.month,
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        try:
            from apps.manutencao.models import AlertaManutencao
            alertas = AlertaManutencao.objects.filter(ativo=True).select_related('veiculo')
            ctx['alertas_vencidos'] = [a for a in alertas if a.vencido]
        except Exception:
            ctx['alertas_vencidos'] = []

        try:
            ctx['multas_pendentes'] = MultaTransito.objects.filter(
                situacao='pendente_identificacao').count()
        except Exception:
            ctx['multas_pendentes'] = 0

        ctx['parcelas_atraso'] = ParcelaContrato.objects.filter(
            situacao='em_atraso',
        ).count()

        return ctx


# ─── Hub de Relatorios ────────────────────────────────────────────────────────

class RelatoriosIndexView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente', 'financeiro']
    template_name = 'core/relatorios/index.html'

    def get(self, request):
        hoje = timezone.now()
        receita_mes = PagamentoContrato.objects.filter(
            data_pagamento__year=hoje.year,
            data_pagamento__month=hoje.month,
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        contratos_mes = Contrato.objects.filter(
            criado_em__year=hoje.year, criado_em__month=hoje.month
        ).count()

        ocupacao_media = self._ocupacao_rapida(hoje.year, hoje.month)

        inadimplencia = ContaReceber.objects.filter(
            situacao='vencido'
        ).aggregate(s=Sum('valor_total') - Sum('valor_pago'))['s'] or Decimal('0.00')

        return render(request, self.template_name, {
            'receita_mes': receita_mes,
            'contratos_mes': contratos_mes,
            'ocupacao_media': ocupacao_media,
            'inadimplencia': inadimplencia,
            'mes_nome': calendar.month_name[hoje.month],
            'ano': hoje.year,
        })

    def _ocupacao_rapida(self, ano, mes):
        """Calcula taxa de ocupacao media usando agregacao no banco (sem N+1)."""
        dias = calendar.monthrange(ano, mes)[1]
        total = Veiculo.objects.exclude(situacao='inativo').count()
        if not total:
            return 0

        contratos = Contrato.objects.filter(
            data_saida__year=ano,
            data_saida__month=mes,
            situacao__in=['encerrado', 'ativo', 'aguardando_devolucao'],
        ).values('veiculo_id').annotate(dias_totais=Sum('total_dias'))

        soma = sum(
            min(c['dias_totais'] or 1, dias) / dias * 100
            for c in contratos
        )
        return round(soma / total, 1)


# ─── Relatorio de Contratos ───────────────────────────────────────────────────

class RelatorioContratosView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente', 'financeiro']
    template_name = 'core/relatorios/contratos.html'
    POR_PAGINA = 50

    def get(self, request):
        ano, mes, periodo = _periodo(request)

        qs = Contrato.objects.select_related('cliente', 'veiculo__grupo')
        if mes:
            qs = qs.filter(criado_em__year=ano, criado_em__month=mes)
        else:
            qs = qs.filter(criado_em__year=ano)

        # Contagens por situacao (uma query com COUNT)
        counts = qs.values('situacao').annotate(n=Count('id'))
        cnt = {c['situacao']: c['n'] for c in counts}
        total      = sum(cnt.values())
        encerrados = cnt.get('encerrado', 0)
        ativos     = cnt.get('ativo', 0) + cnt.get('aguardando_devolucao', 0)
        cancelados = cnt.get('cancelado', 0)
        abertos    = cnt.get('aberto', 0)

        receita = PagamentoContrato.objects.filter(
            contrato__in=qs
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        ticket_medio = receita / encerrados if encerrados else Decimal('0.00')

        media_dias = qs.filter(total_dias__isnull=False).aggregate(
            avg=Avg('total_dias'))['avg'] or 0

        por_canal = qs.exclude(reserva=None).values(
            'reserva__canal'
        ).annotate(total=Count('id')).order_by('-total')

        # Graficos anuais (12 queries em vez de 24 — agrupado)
        labels_mes = [calendar.month_abbr[m] for m in range(1, 13)]
        dados_contratos_mes = [0] * 12
        dados_receita_mes = [0.0] * 12

        por_mes_contratos = Contrato.objects.filter(
            criado_em__year=ano
        ).values('criado_em__month').annotate(n=Count('id'))
        for r in por_mes_contratos:
            dados_contratos_mes[r['criado_em__month'] - 1] = r['n']

        por_mes_receita = PagamentoContrato.objects.filter(
            data_pagamento__year=ano
        ).values('data_pagamento__month').annotate(s=Sum('valor'))
        for r in por_mes_receita:
            dados_receita_mes[r['data_pagamento__month'] - 1] = float(r['s'])

        # Paginacao
        qs_ord = qs.order_by('-criado_em')
        paginator = Paginator(qs_ord, self.POR_PAGINA)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        return render(request, self.template_name, {
            **periodo,
            'total': total, 'encerrados': encerrados, 'ativos': ativos,
            'cancelados': cancelados, 'abertos': abertos,
            'receita': receita, 'ticket_medio': ticket_medio,
            'media_dias': round(media_dias, 1),
            'por_canal': por_canal,
            'page_obj': page_obj,
            'chart_meses': json.dumps({
                'labels': labels_mes,
                'contratos': dados_contratos_mes,
                'receita': dados_receita_mes,
            }),
            'chart_status': json.dumps({
                'labels': ['Encerrado', 'Ativo', 'Cancelado', 'Aberto'],
                'data': [encerrados, ativos, cancelados, abertos],
                'colors': ['#10b981', '#6366f1', '#94a3b8', '#f59e0b'],
            }),
        })


# ─── Relatorio de Frota / Ocupacao ────────────────────────────────────────────

class RelatorioFrotaView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    template_name = 'core/relatorios/frota.html'

    def get(self, request):
        ano, mes, periodo = _periodo(request)
        dias_no_mes = calendar.monthrange(ano, mes)[1] if mes else 365

        veiculos = list(
            Veiculo.objects.exclude(situacao='inativo').select_related('grupo__categoria')
        )
        if not veiculos:
            return render(request, self.template_name, {**periodo,
                'dados_veiculos': [], 'dados_grupo': [], 'ocupacao_media': 0,
                'receita_total': Decimal('0'), 'total_veiculos': 0,
                'chart_ocupacao': '{}', 'chart_receita': '{}'})

        # ── Batch query: todos os contratos do periodo ──
        filtro_c = dict(
            situacao__in=['encerrado', 'ativo', 'aguardando_devolucao'],
            data_saida__year=ano,
        )
        if mes:
            filtro_c['data_saida__month'] = mes

        contratos_agg = (
            Contrato.objects.filter(**filtro_c)
            .values('veiculo_id')
            .annotate(n_locacoes=Count('id'), dias_totais=Sum('total_dias'))
        )
        contratos_map = {r['veiculo_id']: r for r in contratos_agg}

        # Cancelados após checkout — não somam dias, mas são exibidos como coluna informativa
        filtro_cancel = dict(situacao='cancelado', data_saida__isnull=False, data_saida__year=ano)
        if mes:
            filtro_cancel['data_saida__month'] = mes
        cancelamentos_map = {
            r['veiculo_id']: r['n_cancelados']
            for r in Contrato.objects.filter(**filtro_cancel)
            .values('veiculo_id').annotate(n_cancelados=Count('id'))
        }

        # ── Batch query: toda a receita do periodo por veiculo ──
        filtro_p = dict(data_pagamento__year=ano)
        if mes:
            filtro_p['data_pagamento__month'] = mes

        receita_agg = (
            PagamentoContrato.objects.filter(**filtro_p)
            .values('contrato__veiculo_id')
            .annotate(total=Sum('valor'))
        )
        receita_map = {r['contrato__veiculo_id']: r['total'] for r in receita_agg}

        # ── Batch query: despesas por veiculo no periodo ──
        filtro_d = dict(data_competencia__year=ano, veiculo__isnull=False)
        if mes:
            filtro_d['data_competencia__month'] = mes

        despesas_agg = (
            DespesaOperacional.objects.filter(**filtro_d)
            .values('veiculo_id')
            .annotate(total=Sum('valor'))
        )
        despesas_map = {r['veiculo_id']: r['total'] for r in despesas_agg}

        dados = []
        ocupacao_total = Decimal('0')

        for v in veiculos:
            agg = contratos_map.get(v.pk, {})
            n_locacoes = agg.get('n_locacoes', 0)
            dias_locados = min(agg.get('dias_totais') or 0, dias_no_mes)
            receita_v = receita_map.get(v.pk, Decimal('0.00'))
            custo_v = despesas_map.get(v.pk, Decimal('0.00'))
            lucro_v = receita_v - custo_v
            ocupacao = round(dias_locados / dias_no_mes * 100, 1) if dias_no_mes else 0
            ocupacao_total += Decimal(str(ocupacao))

            dados.append({
                'veiculo': v,
                'receita': receita_v,
                'custo': custo_v,
                'lucro': lucro_v,
                'dias_locados': dias_locados,
                'ocupacao': ocupacao,
                'n_locacoes': n_locacoes,
                'ticket_medio': receita_v / n_locacoes if n_locacoes else Decimal('0'),
                'cancelamentos': cancelamentos_map.get(v.pk, 0),
            })

        dados.sort(key=lambda x: x['ocupacao'], reverse=True)
        total_veiculos = len(veiculos)
        ocupacao_media = round(float(ocupacao_total / total_veiculos), 1) if total_veiculos else 0

        top10 = dados[:10]
        chart_ocupacao = json.dumps({
            'labels': [d['veiculo'].placa for d in top10],
            'data':   [d['ocupacao'] for d in top10],
        })
        chart_receita = json.dumps({
            'labels': [d['veiculo'].placa for d in top10],
            'data':   [float(d['receita']) for d in top10],
        })

        from apps.fleet.models import GrupoVeiculo
        grupos = GrupoVeiculo.objects.all()
        dados_grupo = []
        for g in grupos:
            vs = [d for d in dados if d['veiculo'].grupo_id == g.pk]
            if vs:
                dados_grupo.append({
                    'grupo': g,
                    'n': len(vs),
                    'ocupacao_media': round(sum(d['ocupacao'] for d in vs) / len(vs), 1),
                    'receita': sum(d['receita'] for d in vs),
                })

        receita_total = sum(d['receita'] for d in dados)
        custo_total = sum(d['custo'] for d in dados)

        return render(request, self.template_name, {
            **periodo,
            'dados_veiculos': dados,
            'dados_grupo': dados_grupo,
            'ocupacao_media': ocupacao_media,
            'receita_total': receita_total,
            'custo_total': custo_total,
            'lucro_total': receita_total - custo_total,
            'total_veiculos': total_veiculos,
            'chart_ocupacao': chart_ocupacao,
            'chart_receita': chart_receita,
        })


# ─── Relatorio Financeiro (DRE Simplificado) ─────────────────────────────────

class RelatorioFinanceiroView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'core/relatorios/dre.html'

    def get(self, request):
        ano, mes, periodo = _periodo(request, modo='ano')

        pag_qs = PagamentoContrato.objects.filter(data_pagamento__year=ano)
        if mes:
            pag_qs = pag_qs.filter(data_pagamento__month=mes)

        receita_total = pag_qs.aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        receita_por_tipo = pag_qs.values('tipo').annotate(
            total=Sum('valor')
        ).order_by('-total')

        receita_por_forma = pag_qs.values('forma_pagamento').annotate(
            total=Sum('valor')
        ).order_by('-total')

        from apps.financeiro.models import ParcelaDespesa as PD

        # Despesas simples (não parceladas) pelo mês de competência
        simple_qs = DespesaOperacional.objects.filter(parcelado=False, data_competencia__year=ano)
        if mes:
            simple_qs = simple_qs.filter(data_competencia__month=mes)

        # Parcelas de despesas parceladas pelo mês de vencimento — igual ao Fluxo de Caixa
        parcela_qs = PD.objects.filter(data_vencimento__year=ano)
        if mes:
            parcela_qs = parcela_qs.filter(data_vencimento__month=mes)

        despesa_total = (
            (simple_qs.aggregate(s=Sum('valor'))['s'] or Decimal('0.00')) +
            (parcela_qs.aggregate(s=Sum('valor'))['s'] or Decimal('0.00'))
        )

        cat_totals = defaultdict(Decimal)
        for r in simple_qs.values('categoria').annotate(t=Sum('valor')):
            cat_totals[r['categoria']] += r['t']
        for r in parcela_qs.values('despesa__categoria').annotate(t=Sum('valor')):
            cat_totals[r['despesa__categoria']] += r['t']
        despesa_por_cat = sorted(
            [{'categoria': k, 'total': v} for k, v in cat_totals.items()],
            key=lambda x: -x['total'],
        )

        multas_receita = MultaTransito.objects.filter(
            situacao='cobrada_cliente', data_infracao__year=ano,
            **({'data_infracao__month': mes} if mes else {}),
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        resultado = receita_total - despesa_total + multas_receita
        margem = round(float(resultado / receita_total * 100), 1) if receita_total else 0

        # Graficos — 2 queries agrupadas em vez de 24 iteracoes
        labels_mes = [calendar.month_abbr[m] for m in range(1, 13)]
        receitas_mes = [0.0] * 12
        despesas_mes = [0.0] * 12

        for r in PagamentoContrato.objects.filter(data_pagamento__year=ano).values(
            'data_pagamento__month'
        ).annotate(s=Sum('valor')):
            receitas_mes[r['data_pagamento__month'] - 1] = float(r['s'])

        for r in DespesaOperacional.objects.filter(parcelado=False, data_competencia__year=ano).values(
            'data_competencia__month'
        ).annotate(s=Sum('valor')):
            despesas_mes[r['data_competencia__month'] - 1] += float(r['s'])

        for r in PD.objects.filter(data_vencimento__year=ano).values(
            'data_vencimento__month'
        ).annotate(s=Sum('valor')):
            despesas_mes[r['data_vencimento__month'] - 1] += float(r['s'])

        resultados_mes = [r - d for r, d in zip(receitas_mes, despesas_mes)]

        from apps.financeiro.models import DespesaOperacional as DO
        cat_labels = dict(DO.CATEGORIA)
        tipo_labels = dict(PagamentoContrato.TIPO)
        forma_labels = dict(PagamentoContrato.FORMA)

        return render(request, self.template_name, {
            **periodo,
            'receita_total': receita_total,
            'despesa_total': despesa_total,
            'multas_receita': multas_receita,
            'resultado': resultado,
            'margem': margem,
            'receita_por_tipo': receita_por_tipo,
            'receita_por_forma': receita_por_forma,
            'despesa_por_cat': despesa_por_cat,
            'cat_labels': cat_labels,
            'tipo_labels': tipo_labels,
            'forma_labels': forma_labels,
            'chart_dre': json.dumps({
                'labels': labels_mes,
                'receitas': receitas_mes,
                'despesas': despesas_mes,
                'resultados': resultados_mes,
            }),
        })


# ─── Relatorio de Clientes ────────────────────────────────────────────────────

class RelatorioClientesView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
    template_name = 'core/relatorios/clientes.html'

    def get(self, request):
        ano, mes, periodo = _periodo(request, modo='ano')

        contratos_qs = Contrato.objects.filter(situacao='encerrado', criado_em__year=ano)
        if mes:
            contratos_qs = contratos_qs.filter(criado_em__month=mes)

        # Anotacao com receita em uma unica query (sem loop N+1)
        clientes_agg = (
            contratos_qs
            .values(
                'cliente__id', 'cliente__nome', 'cliente__razao_social',
                'cliente__tipo', 'cliente__cpf', 'cliente__cnpj',
            )
            .annotate(
                locacoes=Count('id'),
                receita=Coalesce(Sum('pagamentos__valor'), Decimal('0.00')),
            )
            .order_by('-receita')[:20]
        )

        clientes_receita = [
            {
                **item,
                'ticket_medio': (
                    item['receita'] / item['locacoes'] if item['locacoes'] else Decimal('0')
                ),
                'nome_exibicao': item['cliente__razao_social'] or item['cliente__nome'],
                'documento': item['cliente__cnpj'] or item['cliente__cpf'],
            }
            for item in clientes_agg
        ]

        todos_ids = set(contratos_qs.values_list('cliente_id', flat=True))
        anteriores = set(
            Contrato.objects.filter(
                situacao='encerrado', criado_em__year__lt=ano
            ).values_list('cliente_id', flat=True)
        )
        novos = len(todos_ids - anteriores)
        recorrentes = len(todos_ids & anteriores)

        por_tipo = Cliente.objects.values('tipo').annotate(total=Count('id'))

        top10 = clientes_receita[:10]
        chart_clientes = json.dumps({
            'labels': [c['nome_exibicao'][:20] for c in top10],
            'receita': [float(c['receita']) for c in top10],
            'locacoes': [c['locacoes'] for c in top10],
        })

        return render(request, self.template_name, {
            **periodo,
            'clientes_receita': clientes_receita,
            'total_ativos': len(todos_ids),
            'novos': novos,
            'recorrentes': recorrentes,
            'por_tipo': por_tipo,
            'total_clientes': Cliente.objects.count(),
            'chart_clientes': chart_clientes,
        })


# ─── Relatorio de Inadimplencia ───────────────────────────────────────────────

class RelatorioInadimplenciaView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'core/relatorios/inadimplencia.html'
    POR_PAGINA = 50

    def get(self, request):
        hoje = timezone.now().date()
        situacao_filtro = request.GET.get('situacao', '')

        contas = ContaReceber.objects.select_related(
            'cliente', 'contrato__veiculo'
        ).exclude(situacao__in=['pago', 'cancelado'])

        if situacao_filtro:
            contas = contas.filter(situacao=situacao_filtro)

        todas = list(contas)
        vencidas = [c for c in todas if c.vencida]
        a_vencer = [c for c in todas if not c.vencida]

        aging = {'0_30': [], '31_60': [], '61_90': [], 'mais90': []}
        for c in vencidas:
            d = c.dias_em_atraso
            bucket = '0_30' if d <= 30 else '31_60' if d <= 60 else '61_90' if d <= 90 else 'mais90'
            aging[bucket].append(c)

        total_em_aberto  = sum(c.valor_saldo for c in todas)
        total_vencido    = sum(c.valor_saldo for c in vencidas)
        total_a_vencer   = sum(c.valor_saldo for c in a_vencer)
        inadimplencia_pct = round(
            float(total_vencido / total_em_aberto * 100), 1
        ) if total_em_aberto else 0

        aging_rows = [
            {'key': '0_30',   'label': '1 - 30 dias',     'cor': '#f59e0b',
             'contas': aging['0_30'],   'total': sum(c.valor_saldo for c in aging['0_30'])},
            {'key': '31_60',  'label': '31 - 60 dias',    'cor': '#f97316',
             'contas': aging['31_60'],  'total': sum(c.valor_saldo for c in aging['31_60'])},
            {'key': '61_90',  'label': '61 - 90 dias',    'cor': '#ef4444',
             'contas': aging['61_90'],  'total': sum(c.valor_saldo for c in aging['61_90'])},
            {'key': 'mais90', 'label': 'Mais de 90 dias', 'cor': '#dc2626',
             'contas': aging['mais90'], 'total': sum(c.valor_saldo for c in aging['mais90'])},
        ]

        chart_aging = json.dumps({
            'labels': ['1-30 dias', '31-60 dias', '61-90 dias', '+90 dias'],
            'valores': [float(r['total']) for r in aging_rows],
            'qtd':     [len(r['contas']) for r in aging_rows],
        })

        # Paginacao das contas vencidas
        paginator = Paginator(sorted(vencidas, key=lambda x: x.data_vencimento), self.POR_PAGINA)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        return render(request, self.template_name, {
            'todas': todas,
            'vencidas': vencidas,
            'a_vencer': a_vencer,
            'aging': aging,
            'aging_rows': aging_rows,
            'page_obj': page_obj,
            'total_em_aberto': total_em_aberto,
            'total_vencido': total_vencido,
            'total_a_vencer': total_a_vencer,
            'inadimplencia_pct': inadimplencia_pct,
            'situacao_filtro': situacao_filtro,
            'chart_aging': chart_aging,
            'hoje': hoje,
        })


# ─── Exportacao CSV ───────────────────────────────────────────────────────────

class ExportarCSVView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def get(self, request, tipo):
        handler = {
            'contratos':     self._contratos,
            'frota':         self._frota,
            'inadimplencia': self._inadimplencia,
            'clientes':      self._clientes,
        }.get(tipo)

        if not handler:
            from django.http import HttpResponseNotFound
            return HttpResponseNotFound('Tipo de relatorio nao encontrado.')
        return handler(request)

    def _csv_response(self, nome_arquivo):
        resp = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        resp['Content-Disposition'] = f'attachment; filename="{nome_arquivo}"'
        return resp

    def _contratos(self, request):
        ano, mes, _ = _periodo(request)
        qs = Contrato.objects.select_related('cliente', 'veiculo').filter(
            criado_em__year=ano,
            **({'criado_em__month': mes} if mes else {})
        ).prefetch_related('pagamentos', 'adicionais', 'avarias').order_by('-criado_em')

        resp = self._csv_response(f'contratos_{ano}_{mes or "ano"}.csv')
        w = csv.writer(resp)
        w.writerow(['Numero', 'Cliente', 'Documento', 'Placa', 'Modelo',
                    'Situacao', 'Saida', 'Devolucao', 'Dias', 'Diaria',
                    'Total Locacao', 'Total Pago', 'Saldo'])
        for c in qs:
            w.writerow([
                c.numero, c.cliente.nome_exibicao, c.cliente.documento,
                c.veiculo.placa, f'{c.veiculo.marca} {c.veiculo.modelo}',
                c.get_situacao_display(),
                c.data_saida.strftime('%d/%m/%Y %H:%M') if c.data_saida else '',
                c.data_devolucao_real.strftime('%d/%m/%Y %H:%M') if c.data_devolucao_real else '',
                c.total_dias or '',
                f'{c.diaria:.2f}',
                f'{c.total_geral:.2f}',
                f'{c.total_pago:.2f}',
                f'{c.saldo_devedor:.2f}',
            ])
        return resp

    def _frota(self, request):
        ano, mes, _ = _periodo(request)
        dias_no_mes = calendar.monthrange(ano, mes)[1] if mes else 365
        veiculos = Veiculo.objects.exclude(situacao='inativo').select_related('grupo__categoria')

        filtro_c = dict(situacao__in=['encerrado', 'ativo', 'aguardando_devolucao'], data_saida__year=ano)
        if mes:
            filtro_c['data_saida__month'] = mes
        contratos_agg = (Contrato.objects.filter(**filtro_c)
                         .values('veiculo_id')
                         .annotate(n=Count('id'), dias=Sum('total_dias')))
        contratos_map = {r['veiculo_id']: r for r in contratos_agg}

        filtro_p = dict(data_pagamento__year=ano)
        if mes:
            filtro_p['data_pagamento__month'] = mes
        receita_map = {
            r['contrato__veiculo_id']: r['total']
            for r in PagamentoContrato.objects.filter(**filtro_p)
            .values('contrato__veiculo_id').annotate(total=Sum('valor'))
        }

        resp = self._csv_response(f'frota_{ano}_{mes or "ano"}.csv')
        w = csv.writer(resp)
        w.writerow(['Placa', 'Marca', 'Modelo', 'Ano', 'Grupo', 'Categoria',
                    'Dias Locados', 'Ocupacao %', 'No Locacoes', 'Receita R$'])
        for v in veiculos:
            agg = contratos_map.get(v.pk, {})
            dias = min(agg.get('dias') or 0, dias_no_mes)
            ocup = round(dias / dias_no_mes * 100, 1) if dias_no_mes else 0
            receita = receita_map.get(v.pk, Decimal('0.00'))
            w.writerow([v.placa, v.marca, v.modelo, v.ano_modelo,
                        v.grupo.nome, v.grupo.categoria.nome,
                        dias, ocup, agg.get('n', 0), f'{receita:.2f}'])
        return resp

    def _inadimplencia(self, request):
        contas = list(ContaReceber.objects.select_related(
            'cliente', 'contrato__veiculo'
        ).exclude(situacao__in=['pago', 'cancelado']))

        resp = self._csv_response('inadimplencia.csv')
        w = csv.writer(resp)
        w.writerow(['Contrato', 'Cliente', 'Documento', 'Telefone', 'Placa',
                    'Vencimento', 'Dias Atraso', 'Total R$', 'Pago R$', 'Saldo R$', 'Situacao'])
        for c in sorted(contas, key=lambda x: x.data_vencimento):
            w.writerow([
                c.contrato.numero, c.cliente.nome_exibicao, c.cliente.documento,
                c.cliente.celular or c.cliente.telefone, c.contrato.veiculo.placa,
                c.data_vencimento.strftime('%d/%m/%Y'), c.dias_em_atraso,
                f'{c.valor_total:.2f}', f'{c.valor_pago:.2f}', f'{c.valor_saldo:.2f}',
                c.get_situacao_display(),
            ])
        return resp

    def _clientes(self, request):
        clientes = Cliente.objects.all().order_by('nome')
        resp = self._csv_response('clientes.csv')
        w = csv.writer(resp)
        w.writerow(['Nome/Razao Social', 'Tipo', 'CPF/CNPJ', 'Email',
                    'Telefone', 'Celular', 'Cidade', 'Estado', 'Situacao'])
        for c in clientes:
            w.writerow([c.nome_exibicao, c.get_tipo_display(), c.documento,
                        c.email, c.telefone, c.celular, c.cidade, c.estado,
                        c.get_situacao_display()])
        return resp


RelatorioMensalView = RelatorioFrotaView


# ─── Busca Global ─────────────────────────────────────────────────────────────

class BuscaGlobalView(LoginRequiredMixin, View):
    template_name = 'core/busca.html'

    def get(self, request):
        q = request.GET.get('q', '').strip()
        if not q or len(q) < 2:
            return render(request, self.template_name, {'q': q})

        veiculos = Veiculo.objects.filter(
            Q(placa__icontains=q) | Q(marca__icontains=q) | Q(modelo__icontains=q)
        ).select_related('grupo')[:10]

        clientes = Cliente.objects.filter(
            Q(nome__icontains=q) | Q(razao_social__icontains=q) |
            Q(cpf__icontains=q) | Q(cnpj__icontains=q) | Q(email__icontains=q)
        )[:10]

        contratos = Contrato.objects.select_related('cliente', 'veiculo').filter(
            Q(numero__icontains=q) |
            Q(cliente__nome__icontains=q) |
            Q(cliente__razao_social__icontains=q) |
            Q(veiculo__placa__icontains=q)
        ).order_by('-criado_em')[:10]

        total = len(veiculos) + len(clientes) + len(contratos)

        return render(request, self.template_name, {
            'q': q,
            'veiculos': veiculos,
            'clientes': clientes,
            'contratos': contratos,
            'total': total,
        })


# ─── Manual do Usuário em PDF ─────────────────────────────────────────────────

class ManualUsuarioPDFView(LoginRequiredMixin, View):
    def get(self, request):
        from io import BytesIO
        from xhtml2pdf import pisa
        from django.template.loader import render_to_string

        hoje = timezone.now()
        html = render_to_string('core/manual_pdf.html', {
            'data_geracao': hoje.strftime('%d/%m/%Y'),
            'ano': hoje.year,
        }, request=request)

        buffer = BytesIO()
        pisa.pisaDocument(BytesIO(html.encode('utf-8')), buffer)
        pdf = buffer.getvalue()
        buffer.close()

        resposta = HttpResponse(pdf, content_type='application/pdf')
        resposta['Content-Disposition'] = 'inline; filename="manual-alugafacil.pdf"'
        return resposta
