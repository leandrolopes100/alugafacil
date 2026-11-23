from datetime import timedelta
from django.utils import timezone

_CACHE_ATTR = '_aluga_facil_badges'


def _nome_do_perfil(usuario):
    if usuario.is_superuser:
        return 'Administrador'
    nomes = [g.perfil.nome if hasattr(g, 'perfil') else g.name for g in usuario.groups.select_related('perfil')]
    return ', '.join(nomes) or 'Sem perfil'


def agenda_cobrancas(request):
    # Cache por request para evitar 3 COUNTs duplicados em cada sub-request/context-processor
    cached = getattr(request, _CACHE_ATTR, None)
    if cached is not None:
        return cached

    cobrancas_badge = 0
    pagamentos_badge = 0
    investidores_badge = 0
    modulo_investidores = False
    agenda_url = '/financeiro/agenda/'
    agenda_pagamentos_url = '/financeiro/agenda-pagamentos/'
    pode_configurar_locadora = False
    perfil_usuario = ''
    if request.user.is_authenticated:
        perfil_usuario = _nome_do_perfil(request.user)
        pode_configurar_locadora = request.user.has_perm('core.admin_configuracoes')
        try:
            from apps.contracts.models import ParcelaContrato
            from apps.financeiro.models import ParcelaDespesa
            from apps.financeiro.models import ConfiguracaoLocadora, cobrancas_gestao_consideradas
            from django.urls import reverse
            hoje = timezone.localdate()
            fim_semana = hoje + timedelta(days=7)
            cobrancas_badge = ParcelaContrato.objects.filter(
                situacao__in=['pendente', 'em_atraso'],
                data_vencimento__lte=fim_semana,
            ).count()
            pagamentos_badge = ParcelaDespesa.objects.filter(
                situacao__in=['pendente', 'em_atraso'],
                data_vencimento__lte=fim_semana,
            ).count()
            modulo_investidores = ConfiguracaoLocadora.modulo_investidores_ativo()
            investidores_badge = cobrancas_gestao_consideradas().filter(
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
        'modulo_investidores': modulo_investidores,
        'agenda_url': agenda_url,
        'agenda_pagamentos_url': agenda_pagamentos_url,
        'pode_configurar_locadora': pode_configurar_locadora,
        'perfil_usuario': perfil_usuario,
    }
    try:
        setattr(request, _CACHE_ATTR, result)
    except Exception:
        pass
    return result
