from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import ExtractMonth

from apps.contracts.models import PagamentoContrato

from .models import DespesaOperacional, ParcelaDespesa, cobrancas_gestao_consideradas

CATEGORIA_REPASSE = 'repasse_investidor'
ZERO = Decimal('0.00')


def _no_periodo(campo, ano, mes):
    filtro = {f'{campo}__year': ano}
    if mes:
        filtro[f'{campo}__month'] = mes
    return filtro


def _repasses_pagos():
    return cobrancas_gestao_consideradas().filter(situacao='pago')


def receitas_recebidas(ano, mes=None):
    return PagamentoContrato.objects.receita().filter(**_no_periodo('data_pagamento', ano, mes))


def despesas_pagas_por_categoria(ano, mes=None):
    periodo = _no_periodo('data_pagamento', ano, mes)
    totais = defaultdict(Decimal)
    simples = DespesaOperacional.objects.exclude(categoria='caucao').filter(parcelado=False, **periodo)
    for linha in simples.values('categoria').annotate(t=Sum('valor')):
        totais[linha['categoria']] += linha['t']
    parcelas = ParcelaDespesa.objects.exclude(despesa__categoria='caucao').filter(situacao='pago', **periodo)
    for linha in parcelas.values('despesa__categoria').annotate(t=Sum('valor')):
        totais[linha['despesa__categoria']] += linha['t']
    repasses = _repasses_pagos().filter(**periodo).aggregate(t=Sum('valor'))['t']
    if repasses:
        totais[CATEGORIA_REPASSE] += repasses
    return dict(totais)


def resultado_caixa(ano, mes=None):
    receita = receitas_recebidas(ano, mes).aggregate(s=Sum('valor'))['s'] or ZERO
    por_categoria = despesas_pagas_por_categoria(ano, mes)
    despesa = sum(por_categoria.values(), ZERO)
    return {
        'receita': receita,
        'despesa': despesa,
        'despesa_por_categoria': por_categoria,
        'resultado': receita - despesa,
    }


def resultado_caixa_por_mes(ano):
    receitas = [ZERO] * 12
    despesas = [ZERO] * 12
    for linha in receitas_recebidas(ano).annotate(m=ExtractMonth('data_pagamento')).values('m').annotate(t=Sum('valor')):
        receitas[linha['m'] - 1] += linha['t']
    periodo = _no_periodo('data_pagamento', ano, None)
    fontes = (
        DespesaOperacional.objects.exclude(categoria='caucao').filter(parcelado=False, **periodo),
        ParcelaDespesa.objects.exclude(despesa__categoria='caucao').filter(situacao='pago', **periodo),
        _repasses_pagos().filter(**periodo),
    )
    for fonte in fontes:
        for linha in fonte.annotate(m=ExtractMonth('data_pagamento')).values('m').annotate(t=Sum('valor')):
            despesas[linha['m'] - 1] += linha['t']
    return receitas, despesas
