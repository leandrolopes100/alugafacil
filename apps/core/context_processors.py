from datetime import timedelta
from django.utils import timezone

_CACHE_ATTR = '_aluga_facil_badges'


def agenda_cobrancas(request):
    # Cache por request para evitar 3 COUNTs duplicados em cada sub-request/context-processor
    cached = getattr(request, _CACHE_ATTR, None)
    if cached is not None:
        return cached

    cobrancas_badge = 0
    pagamentos_badge = 0
    investidores_badge = 0
    agenda_url = '/financeiro/agenda/'
    agenda_pagamentos_url = '/financeiro/agenda-pagamentos/'
    if request.user.is_authenticated:
        try:
            from apps.contracts.models import ParcelaContrato
            from apps.financeiro.models import ParcelaDespesa
            from apps.investidores.models import CobrancaGestao
            from django.urls import reverse
            hoje = timezone.now().date()
            fim_semana = hoje + timedelta(days=7)
            cobrancas_badge = ParcelaContrato.objects.filter(
                situacao__in=['pendente', 'em_atraso'],
                data_vencimento__lte=fim_semana,
            ).count()
            pagamentos_badge = ParcelaDespesa.objects.filter(
                situacao__in=['pendente', 'em_atraso'],
                data_vencimento__lte=fim_semana,
            ).count()
            investidores_badge = CobrancaGestao.objects.filter(
                situacao='pendente',
                data_vencimento__lt=hoje,
            ).count()
            agenda_url = reverse('financeiro:agenda')
            agenda_pagamentos_url = reverse('financeiro:agenda-pagamentos')
        except Exception:
            pass

    result = {
        'cobrancas_badge': cobrancas_badge,
        'pagamentos_badge': pagamentos_badge,
        'investidores_badge': investidores_badge,
        'agenda_url': agenda_url,
        'agenda_pagamentos_url': agenda_pagamentos_url,
    }
    try:
        setattr(request, _CACHE_ATTR, result)
    except Exception:
        pass
    return result
