from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import AdicionalContrato, AvariaContrato, Contrato, PagamentoContrato, ParcelaContrato


def _criar_conta_receber(contrato, data_vencimento):
    from apps.financeiro.models import ContaReceber
    ContaReceber.objects.create(
        contrato=contrato,
        cliente=contrato.cliente,
        descricao=f'Locacao - Contrato {contrato.numero}',
        valor_total=contrato.total_geral,
        data_emissao=timezone.localdate(contrato.data_saida),
        data_vencimento=data_vencimento,
        situacao='pendente',
    )
    contrato.sincronizar_conta_receber()


@receiver(post_save, sender=Contrato)
def contrato_post_save(sender, instance, created, **kwargs):
    from apps.financeiro.models import ContaReceber

    instance.limpar_cache_financeiro()

    if instance.situacao == 'ativo' and instance.data_saida:
        novo_vencimento = timezone.localdate(instance.data_devolucao_prevista)
        with transaction.atomic():
            if ContaReceber.objects.select_for_update().filter(contrato=instance).exists():
                instance.sincronizar_conta_receber(novo_vencimento=novo_vencimento)
            else:
                _criar_conta_receber(instance, novo_vencimento)

    elif instance.situacao in ('aguardando_devolucao', 'encerrado'):
        with transaction.atomic():
            if ContaReceber.objects.select_for_update().filter(contrato=instance).exists():
                instance.sincronizar_conta_receber()
            elif instance.data_saida:
                _criar_conta_receber(instance, timezone.localdate(
                    instance.data_devolucao_real or instance.data_devolucao_prevista
                ))
        if instance.situacao == 'encerrado' and instance.reserva_id:
            from .models import Reserva
            Reserva.objects.filter(pk=instance.reserva_id, situacao='ativa').update(situacao='concluida')

    elif instance.situacao == 'cancelado':
        ContaReceber.objects.filter(
            contrato=instance
        ).exclude(situacao='cancelado').update(situacao='cancelado')


@receiver(post_save, sender=PagamentoContrato)
@receiver(post_delete, sender=PagamentoContrato)
@receiver(post_save, sender=ParcelaContrato)
@receiver(post_delete, sender=ParcelaContrato)
@receiver(post_save, sender=AdicionalContrato)
@receiver(post_delete, sender=AdicionalContrato)
@receiver(post_save, sender=AvariaContrato)
@receiver(post_delete, sender=AvariaContrato)
def lancamento_financeiro_alterado(sender, instance, **kwargs):
    from apps.financeiro.models import ContaReceber
    ContaReceber.sincronizar(instance.contrato_id)


@receiver(pre_save, sender=Contrato)
def contrato_caucao_pre_save(sender, instance, **kwargs):
    """Captura caucao_situacao anterior para detectar transicoes no post_save."""
    if instance.pk:
        try:
            instance._caucao_situacao_anterior = (
                Contrato.objects.values_list('caucao_situacao', flat=True).get(pk=instance.pk)
            )
        except Contrato.DoesNotExist:
            instance._caucao_situacao_anterior = None
    else:
        instance._caucao_situacao_anterior = None


@receiver(post_save, sender=Contrato)
def contrato_caucao_post_save(sender, instance, created, **kwargs):
    """
    Reage a mudancas em caucao_situacao:
    - devolvido / devolvido_parcial: registra saida no financeiro (DespesaOperacional).
    A retencao com avaria e tratada em ContratoEncerrarView (avaliada no encerramento).

    Para devolucao parcial, ContratoEncerrarView seta o atributo transiente
    _caucao_valor_devolvido com o valor efetivamente devolvido (caucao - retido).
    Sem esse atributo (devolucao total, ou qualquer outro caminho que sete
    caucao_situacao diretamente), o comportamento padrao e usar caucao_valor
    inteiro, como sempre foi.
    """
    if created or not instance.caucao_valor:
        return

    anterior = getattr(instance, '_caucao_situacao_anterior', None)
    atual = instance.caucao_situacao

    if anterior == atual or atual not in ('devolvido', 'devolvido_parcial'):
        return

    from apps.financeiro.models import DespesaOperacional

    marker = f'[caucao:{instance.numero}]'
    if DespesaOperacional.objects.filter(observacoes__contains=marker).exists():
        return

    DespesaOperacional.objects.create(
        categoria='caucao',
        descricao=f'Devolucao de caucao — Contrato {instance.numero} ({instance.cliente})',
        valor=getattr(instance, '_caucao_valor_devolvido', instance.caucao_valor),
        data_competencia=timezone.localdate(),
        data_pagamento=timezone.localdate(),
        observacoes=marker,
    )


@receiver(post_save, sender=Contrato)
def contrato_historico_km(sender, instance, **kwargs):
    """Registra historico de KM na saida e devolucao do contrato."""
    from apps.fleet.models import HistoricoKmVeiculo

    if instance.km_saida and instance.data_saida:
        HistoricoKmVeiculo.objects.get_or_create(
            veiculo=instance.veiculo,
            contrato=instance,
            origem='contrato_saida',
            defaults={
                'km': instance.km_saida,
                'data': instance.data_saida,
            }
        )

    if instance.km_devolucao and instance.data_devolucao_real:
        HistoricoKmVeiculo.objects.get_or_create(
            veiculo=instance.veiculo,
            contrato=instance,
            origem='contrato_devolucao',
            defaults={
                'km': instance.km_devolucao,
                'data': instance.data_devolucao_real,
            }
        )
