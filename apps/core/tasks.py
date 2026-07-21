from celery import shared_task
from django.utils import timezone


@shared_task(name='apps.core.tasks.marcar_parcelas_atrasadas')
def marcar_parcelas_atrasadas():
    """Marca como em_atraso todas as parcelas pendentes vencidas."""
    from apps.contracts.models import ParcelaContrato
    hoje = timezone.now().date()
    return ParcelaContrato.objects.filter(
        situacao='pendente',
        data_vencimento__lt=hoje,
    ).update(situacao='em_atraso')


@shared_task(name='apps.core.tasks.marcar_contas_vencidas')
def marcar_contas_vencidas():
    """Marca como vencido as ContaReceber pendentes com vencimento ultrapassado."""
    from apps.financeiro.models import ContaReceber
    hoje = timezone.now().date()
    ContaReceber.objects.filter(
        situacao__in=['pendente', 'pago_parcial'],
        data_vencimento__lt=hoje,
    ).update(situacao='vencido')


@shared_task(name='apps.core.tasks.sincronizar_despesas_auto')
def sincronizar_despesas_auto():
    """Sincroniza pagamentos automaticos de despesas operacionais."""
    from apps.financeiro.models import DespesaOperacional
    DespesaOperacional.sincronizar_auto_pagamento()


@shared_task(name='apps.core.tasks.marcar_parcelas_despesa_atrasadas')
def marcar_parcelas_despesa_atrasadas():
    """Marca como em_atraso todas as parcelas de despesa pendentes vencidas."""
    from apps.financeiro.models import ParcelaDespesa
    hoje = timezone.now().date()
    ParcelaDespesa.objects.filter(
        situacao='pendente',
        data_vencimento__lt=hoje,
    ).update(situacao='em_atraso')


@shared_task(name='apps.core.tasks.alertar_documentos_vencendo')
def alertar_documentos_vencendo():
    """Registra no log documentos de veiculos que vencerao em breve."""
    import logging
    logger = logging.getLogger(__name__)
    from apps.fleet.models import DocumentoVeiculo
    hoje = timezone.now().date()
    docs = DocumentoVeiculo.objects.select_related('veiculo').filter(
        data_validade__isnull=False
    )
    for doc in docs:
        if doc.proximo_vencimento or doc.vencido:
            status = 'VENCIDO' if doc.vencido else 'VENCENDO'
            logger.warning(
                'Documento %s — %s | Veiculo %s | Validade: %s [%s]',
                doc.get_tipo_display(), doc.numero or '-',
                doc.veiculo.placa, doc.data_validade, status,
            )
