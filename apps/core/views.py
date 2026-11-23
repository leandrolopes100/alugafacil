import calendar
import csv
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from apps.core.mixins import PermissaoRequeridaMixin
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dates import MONTHS, MONTHS_3
from django.views.generic import TemplateView, View

from apps.contracts.models import (
    Contrato, PagamentoContrato, ParcelaContrato, dias_locados_por_veiculo,
)
from apps.customers.models import Cliente
from apps.financeiro.models import ContaReceber, DespesaOperacional, MultaTransito, ParcelaDespesa
from apps.fleet.models import Veiculo


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _limites_periodo(ano, mes):
    if mes:
        return date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])
    return date(ano, 1, 1), date(ano, 12, 31)


def _ocupacao_media(ano, mes):
    veiculos = list(Veiculo.objects.exclude(situacao='inativo').values_list('pk', flat=True))
    dias, dias_periodo = dias_locados_por_veiculo(*_limites_periodo(ano, mes))
    if not veiculos or not dias_periodo:
        return 0
    return round(sum(dias.get(v, 0) for v in veiculos) / (dias_periodo * len(veiculos)) * 100, 1)


def _periodo(request, modo='mes'):
    from apps.core.utils import ler_ano_mes
    hoje = timezone.localdate()
    ano, mes = ler_ano_mes(request.GET, hoje, 0 if modo == 'ano' else hoje.month)
    anos_disp = list(range(max(hoje.year - 4, 2020), hoje.year + 1))
    meses_disp = [(m, MONTHS[m]) for m in range(1, 13)]
    return ano, mes, {
        'ano_sel': ano,
        'mes_sel': mes,
        'anos_disp': anos_disp,
        'meses_disp': meses_disp,
        'mes_nome': MONTHS[mes] if mes else 'Ano completo',
    }


# ─── Dashboard ────────────────────────────────────────────────────────────────

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'core/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        hoje = timezone.localdate()

        # Contagens de veiculos em uma unica query
        situacoes = Veiculo.objects.values('situacao').annotate(n=Count('id'))
        sit_map = {s['situacao']: s['n'] for s in situacoes}
        ctx['veiculos_disponiveis'] = sit_map.get('disponivel', 0)
        ctx['veiculos_em_uso']      = sit_map.get('em_uso', 0)
        ctx['veiculos_manutencao']  = sit_map.get('manutencao', 0)
        ctx['total_veiculos']       = sum(v for k, v in sit_map.items() if k != 'inativo')

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
            Q(situacao='em_atraso') | Q(situacao='pendente', data_vencimento__lt=hoje)
        ).count()

        ctx['taxa_ocupacao'] = _ocupacao_media(hoje.year, hoje.month)

        from apps.financeiro.resultado import resultado_caixa
        caixa = resultado_caixa(hoje.year, hoje.month)
        ctx['receita_mes'] = caixa['receita']
        ctx['desp_pago'] = caixa['despesa']
        ctx['resultado_mes'] = caixa['resultado']

        from apps.financeiro.models import resumo_contas_a_pagar
        a_pagar = resumo_contas_a_pagar(hoje)
        ctx['desp_a_pagar'] = a_pagar['total_mes']
        ctx['desp_atraso'] = a_pagar['total_vencido']


        # ── Parcelas de despesas operacionais vencidas ──
        ctx['parcelas_despesa_atraso'] = ParcelaDespesa.objects.filter(
            Q(situacao='em_atraso') |
            Q(situacao='pendente', data_vencimento__lt=hoje)
        ).count()

        return ctx


# ─── Hub de Relatorios ────────────────────────────────────────────────────────

class RelatoriosIndexView(PermissaoRequeridaMixin, View):
    permissao = ('relatorios_contratos', 'relatorios_clientes', 'relatorios_financeiros')
    template_name = 'core/relatorios/index.html'

    def get(self, request):
        hoje = timezone.localdate()
        receita_mes = PagamentoContrato.objects.receita().filter(
            data_pagamento__year=hoje.year,
            data_pagamento__month=hoje.month,
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        contratos_mes = Contrato.objects.filter(
            criado_em__year=hoje.year, criado_em__month=hoje.month
        ).count()

        ocupacao_media = _ocupacao_media(hoje.year, hoje.month)
        inadimplencia = ContaReceber.resumo_aberto(hoje)['total_vencido']

        return render(request, self.template_name, {
            'receita_mes': receita_mes,
            'contratos_mes': contratos_mes,
            'ocupacao_media': ocupacao_media,
            'inadimplencia': inadimplencia,
            'mes_nome': MONTHS[hoje.month],
            'ano': hoje.year,
        })



# ─── Relatorio de Contratos ───────────────────────────────────────────────────

class RelatorioContratosView(PermissaoRequeridaMixin, View):
    permissao = 'relatorios_contratos'
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

        receita = PagamentoContrato.objects.receita().filter(
            contrato__in=qs
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')
        receita_encerrados = PagamentoContrato.objects.receita().filter(
            contrato__in=qs.filter(situacao='encerrado')
        ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

        ticket_medio = receita_encerrados / encerrados if encerrados else Decimal('0.00')

        media_dias = qs.filter(total_dias__isnull=False).aggregate(
            avg=Avg('total_dias'))['avg'] or 0

        por_canal = qs.exclude(reserva=None).values(
            'reserva__canal'
        ).annotate(total=Count('id')).order_by('-total')

        # Graficos anuais (12 queries em vez de 24 — agrupado)
        labels_mes = [MONTHS_3[m].capitalize() for m in range(1, 13)]
        dados_contratos_mes = [0] * 12
        dados_receita_mes = [0.0] * 12

        por_mes_contratos = Contrato.objects.filter(
            criado_em__year=ano
        ).values('criado_em__month').annotate(n=Count('id'))
        for r in por_mes_contratos:
            dados_contratos_mes[r['criado_em__month'] - 1] = r['n']

        por_mes_receita = PagamentoContrato.objects.receita().filter(
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

class RelatorioFrotaView(PermissaoRequeridaMixin, View):
    permissao = 'relatorios_frota'
    template_name = 'core/relatorios/frota.html'

    def get(self, request):
        from apps.financeiro.resultado import resultado_por_veiculo
        ano, mes, periodo = _periodo(request)
        dias_locados_map, dias_no_mes = dias_locados_por_veiculo(*_limites_periodo(ano, mes))
        financeiro_map = resultado_por_veiculo(ano, mes)

        veiculos = list(
            Veiculo.objects.filter(
                ~Q(situacao='inativo') | Q(pk__in=[pk for pk in financeiro_map if pk is not None])
            ).select_related('grupo__categoria')
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

        zero = Decimal('0.00')
        dados = []
        ocupacao_total = Decimal('0')
        n_ativos = 0

        for v in veiculos:
            agg = contratos_map.get(v.pk, {})
            n_locacoes = agg.get('n_locacoes', 0)
            dias_locados = dias_locados_map.get(v.pk, 0)
            financeiro_v = financeiro_map.get(v.pk, {})
            receita_v = financeiro_v.get('receita', zero)
            custo_direto_v = financeiro_v.get('despesa', zero)
            resultado_v = receita_v - custo_direto_v
            ocupacao = round(dias_locados / dias_no_mes * 100, 1) if dias_no_mes else 0
            if v.situacao != 'inativo':
                ocupacao_total += Decimal(str(ocupacao))
                n_ativos += 1

            dados.append({
                'veiculo': v,
                'receita': receita_v,
                'custo_direto': custo_direto_v,
                'resultado_direto': resultado_v,
                'dias_locados': dias_locados,
                'ocupacao': ocupacao,
                'n_locacoes': n_locacoes,
                'ticket_medio': receita_v / n_locacoes if n_locacoes else Decimal('0'),
                'cancelamentos': cancelamentos_map.get(v.pk, 0),
            })

        dados.sort(key=lambda x: x['ocupacao'], reverse=True)
        total_veiculos = n_ativos
        ocupacao_media = round(float(ocupacao_total / n_ativos), 1) if n_ativos else 0

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

        receita_total = sum((d['receita'] for d in dados), zero)
        custo_direto_total = sum((d['custo_direto'] for d in dados), zero)
        despesa_sem_veiculo = financeiro_map.get(None, {}).get('despesa', zero)

        return render(request, self.template_name, {
            **periodo,
            'dados_veiculos': dados,
            'dados_grupo': dados_grupo,
            'ocupacao_media': ocupacao_media,
            'receita_total': receita_total,
            'custo_direto_total': custo_direto_total,
            'resultado_direto_total': receita_total - custo_direto_total,
            'despesa_sem_veiculo': despesa_sem_veiculo,
            'resultado_geral': receita_total - custo_direto_total - despesa_sem_veiculo,
            'total_veiculos': total_veiculos,
            'chart_ocupacao': chart_ocupacao,
            'chart_receita': chart_receita,
        })


# ─── Relatorio Financeiro (DRE Simplificado) ─────────────────────────────────

class RelatorioFinanceiroView(PermissaoRequeridaMixin, View):
    permissao = 'relatorios_financeiros'
    template_name = 'core/relatorios/dre.html'

    def get(self, request):
        from apps.financeiro.resultado import CATEGORIA_REPASSE, receitas_recebidas, resultado_caixa, resultado_caixa_por_mes
        ano, mes, periodo = _periodo(request, modo='ano')

        pag_qs = receitas_recebidas(ano, mes)
        receita_por_tipo = pag_qs.values('tipo').annotate(total=Sum('valor')).order_by('-total')
        receita_por_forma = pag_qs.values('forma_pagamento').annotate(total=Sum('valor')).order_by('-total')

        caixa = resultado_caixa(ano, mes)
        receita_total = caixa['receita']
        despesa_total = caixa['despesa']
        resultado = caixa['resultado']
        despesa_por_cat = sorted(
            [{'categoria': k, 'total': v} for k, v in caixa['despesa_por_categoria'].items()],
            key=lambda x: -x['total'],
        )
        margem = round(float(resultado / receita_total * 100), 1) if receita_total else 0

        labels_mes = [MONTHS_3[m].capitalize() for m in range(1, 13)]
        receitas, despesas = resultado_caixa_por_mes(ano)
        receitas_mes = [float(v) for v in receitas]
        despesas_mes = [float(v) for v in despesas]
        resultados_mes = [r - d for r, d in zip(receitas_mes, despesas_mes)]

        from apps.financeiro.models import DespesaOperacional as DO
        cat_labels = {**dict(DO.CATEGORIA), CATEGORIA_REPASSE: 'Repasse a investidores'}
        tipo_labels = dict(PagamentoContrato.TIPO)
        forma_labels = dict(PagamentoContrato.FORMA)

        return render(request, self.template_name, {
            **periodo,
            'receita_total': receita_total,
            'despesa_total': despesa_total,
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

class RelatorioClientesView(PermissaoRequeridaMixin, View):
    permissao = 'relatorios_clientes'
    template_name = 'core/relatorios/clientes.html'

    def get(self, request):
        ano, mes, periodo = _periodo(request, modo='ano')

        inicio, fim = _limites_periodo(ano, mes)
        pagamentos = PagamentoContrato.objects.receita().filter(
            data_pagamento__date__gte=inicio, data_pagamento__date__lte=fim,
        )
        clientes_agg = (
            pagamentos
            .values(
                'contrato__cliente__id', 'contrato__cliente__nome', 'contrato__cliente__razao_social',
                'contrato__cliente__tipo', 'contrato__cliente__cpf', 'contrato__cliente__cnpj',
            )
            .annotate(locacoes=Count('contrato', distinct=True), receita=Sum('valor'))
            .order_by('-receita')[:20]
        )

        clientes_receita = [
            {
                'cliente__tipo': item['contrato__cliente__tipo'],
                'locacoes': item['locacoes'],
                'receita': item['receita'],
                'ticket_medio': (
                    item['receita'] / item['locacoes'] if item['locacoes'] else Decimal('0')
                ),
                'nome_exibicao': item['contrato__cliente__razao_social'] or item['contrato__cliente__nome'],
                'documento': item['contrato__cliente__cnpj'] or item['contrato__cliente__cpf'],
            }
            for item in clientes_agg
        ]

        locados = Contrato.objects.filter(
            situacao__in=['ativo', 'aguardando_devolucao', 'encerrado'],
            data_saida__date__lte=fim,
        ).filter(Q(data_devolucao_real__isnull=True) | Q(data_devolucao_real__date__gte=inicio))
        todos_ids = set(locados.values_list('cliente_id', flat=True))
        anteriores = set(
            Contrato.objects.filter(
                cliente_id__in=todos_ids, data_saida__date__lt=inicio,
            ).exclude(situacao='cancelado').values_list('cliente_id', flat=True)
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

class RelatorioInadimplenciaView(PermissaoRequeridaMixin, View):
    permissao = 'relatorios_financeiros'
    template_name = 'core/relatorios/inadimplencia.html'
    POR_PAGINA = 50

    def get(self, request):
        hoje = timezone.localdate()
        situacao_filtro = request.GET.get('situacao', '')

        resumo = ContaReceber.resumo_aberto(hoje)
        todas = resumo['abertas']
        if situacao_filtro:
            todas = [c for c in todas if c.situacao == situacao_filtro]
        vencidas = sorted((c for c in todas if c.tem_atraso), key=lambda c: -c.dias_atraso_efetivo)
        a_vencer = [c for c in todas if not c.tem_atraso]

        aging = {'0_30': [], '31_60': [], '61_90': [], 'mais90': []}
        for c in vencidas:
            d = c.dias_atraso_efetivo
            bucket = '0_30' if d <= 30 else '31_60' if d <= 60 else '61_90' if d <= 90 else 'mais90'
            aging[bucket].append(c)

        total_em_aberto  = sum((c.valor_saldo for c in todas), Decimal('0.00'))
        total_vencido    = sum((c.valor_vencido for c in vencidas), Decimal('0.00'))
        total_vencido_atualizado = sum((c.valor_vencido_atualizado for c in vencidas), Decimal('0.00'))
        total_a_vencer   = total_em_aberto - total_vencido
        inadimplencia_pct = round(
            float(total_vencido / total_em_aberto * 100), 1
        ) if total_em_aberto else 0

        aging_rows = [
            {'key': '0_30',   'label': '1 - 30 dias',     'cor': '#f59e0b',
             'contas': aging['0_30'], 'total': sum(c.valor_vencido for c in aging['0_30'])},
            {'key': '31_60',  'label': '31 - 60 dias',    'cor': '#f97316',
             'contas': aging['31_60'], 'total': sum(c.valor_vencido for c in aging['31_60'])},
            {'key': '61_90',  'label': '61 - 90 dias',    'cor': '#ef4444',
             'contas': aging['61_90'], 'total': sum(c.valor_vencido for c in aging['61_90'])},
            {'key': 'mais90', 'label': 'Mais de 90 dias', 'cor': '#dc2626',
             'contas': aging['mais90'], 'total': sum(c.valor_vencido for c in aging['mais90'])},
        ]

        chart_aging = json.dumps({
            'labels': ['1-30 dias', '31-60 dias', '61-90 dias', '+90 dias'],
            'valores': [float(r['total']) for r in aging_rows],
            'qtd':     [len(r['contas']) for r in aging_rows],
        })

        # Paginacao das contas vencidas
        paginator = Paginator(sorted(vencidas, key=lambda x: -x.dias_atraso_efetivo), self.POR_PAGINA)
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
            'total_vencido_atualizado': total_vencido_atualizado,
            'total_a_vencer': total_a_vencer,
            'inadimplencia_pct': inadimplencia_pct,
            'situacao_filtro': situacao_filtro,
            'chart_aging': chart_aging,
            'hoje': hoje,
        })


# ─── Exportacao CSV ───────────────────────────────────────────────────────────

class ExportarCSVView(PermissaoRequeridaMixin, View):
    permissao = 'relatorios_financeiros'

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
            for r in PagamentoContrato.objects.receita().filter(**filtro_p)
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
        contas = ContaReceber.resumo_aberto()['abertas']

        resp = self._csv_response('inadimplencia.csv')
        w = csv.writer(resp)
        w.writerow(['Contrato', 'Cliente', 'Documento', 'Telefone', 'Placa',
                    'Fim do contrato', 'Dias Atraso', 'Total R$', 'Pago R$', 'Saldo R$',
                    'Vencido R$', 'Situacao'])
        for c in sorted(contas, key=lambda x: -x.dias_atraso_efetivo):
            w.writerow([
                c.contrato.numero, c.cliente.nome_exibicao, c.cliente.documento,
                c.cliente.celular or c.cliente.telefone, c.contrato.veiculo.placa,
                c.data_vencimento.strftime('%d/%m/%Y'), c.dias_atraso_efetivo,
                f'{c.valor_total:.2f}', f'{c.valor_pago:.2f}', f'{c.valor_saldo:.2f}',
                f'{c.valor_vencido:.2f}', c.get_situacao_display(),
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
