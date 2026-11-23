from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import ExtractMonth, TruncMonth
from django.utils import timezone

from apps.contracts.models import PagamentoContrato

from .models import DespesaOperacional, ParcelaDespesa, cobrancas_gestao_consideradas

CATEGORIA_REPASSE = 'repasse_investidor'
SEM_VEICULO = 'sem_veiculo'
ZERO = Decimal('0.00')


def _no_periodo(campo, ano=None, mes=None):
    if not ano:
        return {}
    filtro = {f'{campo}__year': ano}
    if mes:
        filtro[f'{campo}__month'] = mes
    return filtro


def _do_veiculo(campo, veiculo):
    if veiculo is None:
        return {}
    if veiculo == SEM_VEICULO:
        return {f'{campo}__isnull': True}
    return {campo: veiculo}


def _repasses_pagos():
    return cobrancas_gestao_consideradas().filter(situacao='pago')


def _fontes_de_despesa():
    return (
        (DespesaOperacional.objects.exclude(categoria='caucao').filter(parcelado=False), 'categoria', 'veiculo'),
        (ParcelaDespesa.objects.exclude(despesa__categoria='caucao').filter(situacao='pago'),
         'despesa__categoria', 'despesa__veiculo'),
        (_repasses_pagos(), None, 'veiculo_investidor__veiculo'),
    )


def _despesas_pagas(ano=None, mes=None, veiculo=None):
    for queryset, campo_categoria, campo_veiculo in _fontes_de_despesa():
        filtrado = queryset.filter(
            data_pagamento__isnull=False,
            **_no_periodo('data_pagamento', ano, mes),
            **_do_veiculo(campo_veiculo, veiculo),
        )
        yield filtrado, campo_categoria, campo_veiculo


def receitas_recebidas(ano=None, mes=None, veiculo=None):
    return PagamentoContrato.objects.receita().filter(
        **_no_periodo('data_pagamento', ano, mes),
        **_do_veiculo('contrato__veiculo', veiculo),
    )


def receitas_por_tipo(ano=None, mes=None, veiculo=None):
    linhas = receitas_recebidas(ano, mes, veiculo).values('tipo').annotate(t=Sum('valor'))
    return {linha['tipo']: linha['t'] for linha in linhas}


def despesas_pagas_por_categoria(ano=None, mes=None, veiculo=None):
    totais = defaultdict(Decimal)
    for queryset, campo_categoria, _ in _despesas_pagas(ano, mes, veiculo):
        if campo_categoria is None:
            total = queryset.aggregate(t=Sum('valor'))['t']
            if total:
                totais[CATEGORIA_REPASSE] += total
            continue
        for linha in queryset.values(campo_categoria).annotate(t=Sum('valor')):
            totais[linha[campo_categoria]] += linha['t']
    return dict(totais)


def resultado_caixa(ano=None, mes=None, veiculo=None):
    receita = receitas_recebidas(ano, mes, veiculo).aggregate(s=Sum('valor'))['s'] or ZERO
    por_categoria = despesas_pagas_por_categoria(ano, mes, veiculo)
    despesa = sum(por_categoria.values(), ZERO)
    return {
        'receita': receita,
        'despesa': despesa,
        'despesa_por_categoria': por_categoria,
        'resultado': receita - despesa,
    }


def resultado_caixa_por_mes(ano, veiculo=None):
    receitas = [ZERO] * 12
    despesas = [ZERO] * 12
    meses_receita = (
        receitas_recebidas(ano, veiculo=veiculo)
        .annotate(m=ExtractMonth('data_pagamento')).values('m').annotate(t=Sum('valor'))
    )
    for linha in meses_receita:
        receitas[linha['m'] - 1] += linha['t']
    for queryset, _, _ in _despesas_pagas(ano, veiculo=veiculo):
        for linha in queryset.annotate(m=ExtractMonth('data_pagamento')).values('m').annotate(t=Sum('valor')):
            despesas[linha['m'] - 1] += linha['t']
    return receitas, despesas


def resultado_por_veiculo(ano=None, mes=None):
    dados = defaultdict(lambda: {'receita': ZERO, 'despesa': ZERO})
    for linha in receitas_recebidas(ano, mes).values('contrato__veiculo').annotate(t=Sum('valor')):
        dados[linha['contrato__veiculo']]['receita'] += linha['t']
    for queryset, _, campo_veiculo in _despesas_pagas(ano, mes):
        for linha in queryset.values(campo_veiculo).annotate(t=Sum('valor')):
            dados[linha[campo_veiculo]]['despesa'] += linha['t']
    return {
        veiculo_id: {**valores, 'resultado': valores['receita'] - valores['despesa']}
        for veiculo_id, valores in dados.items()
    }


def _primeiro_dia(valor):
    if isinstance(valor, datetime):
        return timezone.localtime(valor).date() if timezone.is_aware(valor) else valor.date()
    return valor


def resultado_mensal(veiculo=None):
    meses = defaultdict(lambda: {'receita': ZERO, 'despesa': ZERO})
    receita_mes = (
        receitas_recebidas(veiculo=veiculo)
        .annotate(m=TruncMonth('data_pagamento')).values('m').annotate(t=Sum('valor'))
    )
    for linha in receita_mes:
        meses[_primeiro_dia(linha['m'])]['receita'] += linha['t']
    for queryset, _, _ in _despesas_pagas(veiculo=veiculo):
        for linha in queryset.annotate(m=TruncMonth('data_pagamento')).values('m').annotate(t=Sum('valor')):
            meses[_primeiro_dia(linha['m'])]['despesa'] += linha['t']
    return [
        {'mes': mes, **valores, 'resultado': valores['receita'] - valores['despesa']}
        for mes, valores in sorted(meses.items(), reverse=True)
    ]


def lancamentos(veiculo, ano=None, mes=None, limite=30):
    from django.urls import reverse
    rotulos_receita = dict(PagamentoContrato.TIPO)
    rotulos_despesa = {**dict(DespesaOperacional.CATEGORIA), CATEGORIA_REPASSE: 'Repasse ao investidor'}
    itens = []
    pagamentos = (
        receitas_recebidas(ano, mes, veiculo).select_related('contrato').order_by('-data_pagamento')[:limite]
    )
    for pagamento in pagamentos:
        itens.append({
            'data': timezone.localtime(pagamento.data_pagamento).date(),
            'descricao': f'Contrato {pagamento.contrato.numero}',
            'categoria': rotulos_receita.get(pagamento.tipo, pagamento.tipo),
            'valor': pagamento.valor,
            'entrada': True,
            'url': reverse('contratos:detalhe', args=[pagamento.contrato_id]),
        })
    simples, parcelas, repasses = (fonte for fonte, _, _ in _despesas_pagas(ano, mes, veiculo))
    for despesa in simples.order_by('-data_pagamento')[:limite]:
        itens.append({
            'data': despesa.data_pagamento, 'descricao': despesa.descricao,
            'categoria': rotulos_despesa.get(despesa.categoria, despesa.categoria),
            'valor': despesa.valor, 'entrada': False,
            'url': reverse('financeiro:despesa-detalhe', args=[despesa.pk]),
        })
    for parcela in parcelas.select_related('despesa').order_by('-data_pagamento')[:limite]:
        itens.append({
            'data': parcela.data_pagamento,
            'descricao': f'{parcela.despesa.descricao} (parcela {parcela.numero})',
            'categoria': rotulos_despesa.get(parcela.despesa.categoria, parcela.despesa.categoria),
            'valor': parcela.valor, 'entrada': False,
            'url': reverse('financeiro:despesa-detalhe', args=[parcela.despesa_id]),
        })
    for repasse in repasses.order_by('-data_pagamento')[:limite]:
        itens.append({
            'data': repasse.data_pagamento,
            'descricao': f'Semana de {repasse.semana_inicio:%d/%m} a {repasse.semana_fim:%d/%m/%Y}',
            'categoria': rotulos_despesa[CATEGORIA_REPASSE],
            'valor': repasse.valor, 'entrada': False, 'url': None,
        })
    itens.sort(key=lambda item: item['data'], reverse=True)
    return itens[:limite]


def painel_do_veiculo(veiculo, ano=None, mes=None):
    rotulos_receita = dict(PagamentoContrato.TIPO)
    rotulos_despesa = {**dict(DespesaOperacional.CATEGORIA), CATEGORIA_REPASSE: 'Repasse ao investidor'}
    periodo = resultado_caixa(ano, mes, veiculo)
    total = periodo if ano is None else resultado_caixa(veiculo=veiculo)
    de_investidor = veiculo.vinculo_investidor.filter(ativo=True).exists()
    retorno = None
    if veiculo.valor_aquisicao and not de_investidor:
        retorno = (total['resultado'] / veiculo.valor_aquisicao * 100).quantize(Decimal('0.1'))
    return {
        'periodo': periodo,
        'total': total,
        'receitas': sorted(
            ((rotulos_receita.get(tipo, tipo), valor) for tipo, valor in receitas_por_tipo(ano, mes, veiculo).items()),
            key=lambda linha: linha[1], reverse=True,
        ),
        'despesas': sorted(
            ((rotulos_despesa.get(cat, cat), valor) for cat, valor in periodo['despesa_por_categoria'].items()),
            key=lambda linha: linha[1], reverse=True,
        ),
        'a_pagar': despesas_a_pagar(veiculo),
        'mensal': resultado_mensal(veiculo)[:24],
        'lancamentos': lancamentos(veiculo, ano, mes),
        'de_investidor': de_investidor,
        'retorno_pct': retorno,
    }


def despesas_a_pagar(veiculo=None):
    simples = DespesaOperacional.objects.exclude(categoria='caucao').filter(
        parcelado=False, data_pagamento__isnull=True, **_do_veiculo('veiculo', veiculo),
    ).aggregate(t=Sum('valor'))['t'] or ZERO
    parcelas = ParcelaDespesa.objects.exclude(despesa__categoria='caucao').exclude(situacao='pago').filter(
        **_do_veiculo('despesa__veiculo', veiculo),
    ).aggregate(t=Sum('valor'))['t'] or ZERO
    repasses = cobrancas_gestao_consideradas().filter(
        situacao='pendente', **_do_veiculo('veiculo_investidor__veiculo', veiculo),
    ).aggregate(t=Sum('valor'))['t'] or ZERO
    return simples + parcelas + repasses
