from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import AdicionalContrato, AvariaContrato, Contrato, PagamentoContrato


@receiver(post_save, sender=Contrato)
def contrato_post_save(sender, instance, created, **kwargs):
    """
    Cria ou atualiza ContaReceber ao mudar situacao do contrato.
    Usa transaction.atomic + select_for_update para evitar race condition.

    - 'ativo': cria/atualiza ContaReceber com valor e data_vencimento atuais (cobre prorrogações).
    - 'encerrado': atualiza com o total final calculado no fechamento.
    - 'cancelado': cancela a ContaReceber para limpar o dashboard financeiro.
    """
    from apps.financeiro.models import ContaReceber

    # Invalida cached_properties para garantir calculo fresco do total
    for attr in ('total_geral', 'total_adicionais', 'total_avarias',
                 'total_locacao', 'total_pago', 'total_caucao_coletado', 'saldo_devedor'):
        instance.__dict__.pop(attr, None)

    if instance.situacao == 'ativo' and instance.data_saida:
        novo_total = instance.total_geral
        novo_vencimento = instance.data_devolucao_prevista.date()
        with transaction.atomic():
            conta_qs = ContaReceber.objects.select_for_update().filter(contrato=instance)
            if conta_qs.exists():
                conta_qs.first().atualizar_situacao(
                    novo_valor_total=novo_total,
                    novo_vencimento=novo_vencimento,
                )
            else:
                ContaReceber.objects.create(
                    contrato=instance,
                    cliente=instance.cliente,
                    descricao=f'Locacao - Contrato {instance.numero}',
                    valor_total=novo_total,
                    data_emissao=instance.data_saida.date(),
                    data_vencimento=novo_vencimento,
                    situacao='pendente',
                )

    elif instance.situacao == 'encerrado':
        novo_total = instance.total_geral
        with transaction.atomic():
            try:
                ContaReceber.objects.select_for_update().get(
                    contrato=instance
                ).atualizar_situacao(novo_valor_total=novo_total)
            except ContaReceber.DoesNotExist:
                if instance.data_saida:
                    ContaReceber.objects.create(
                        contrato=instance,
                        cliente=instance.cliente,
                        descricao=f'Locacao - Contrato {instance.numero}',
                        valor_total=novo_total,
                        data_emissao=instance.data_saida.date(),
                        data_vencimento=(
                            instance.data_devolucao_real.date()
                            if instance.data_devolucao_real
                            else instance.data_devolucao_prevista.date()
                        ),
                        situacao='pendente',
                    )
        # Reserva vinculada → concluida (fecha o ciclo da reserva)
        if instance.reserva_id:
            from .models import Reserva
            Reserva.objects.filter(pk=instance.reserva_id, situacao='ativa').update(situacao='concluida')

    elif instance.situacao == 'cancelado':
        # Remove a conta do radar financeiro ao cancelar o contrato
        ContaReceber.objects.filter(
            contrato=instance
        ).exclude(situacao='cancelado').update(situacao='cancelado')


@receiver(post_save, sender=PagamentoContrato)
def pagamento_post_save(sender, instance, created, **kwargs):
    """Atualiza ContaReceber sempre que um pagamento e registrado."""
    from apps.financeiro.models import ContaReceber
    with transaction.atomic():
        try:
            conta = ContaReceber.objects.select_for_update().get(contrato=instance.contrato)
            conta.atualizar_situacao()
        except ContaReceber.DoesNotExist:
            pass


@receiver(post_save, sender=AdicionalContrato)
@receiver(post_save, sender=AvariaContrato)
def adicional_avaria_post_save(sender, instance, **kwargs):
    """Atualiza ContaReceber quando um adicional ou avaria é adicionado ao contrato."""
    from apps.financeiro.models import ContaReceber

    contrato = instance.contrato
    for attr in ('total_geral', 'total_adicionais', 'total_avarias',
                 'total_locacao', 'total_pago', 'total_caucao_coletado', 'saldo_devedor'):
        contrato.__dict__.pop(attr, None)

    novo_total = contrato.total_geral
    with transaction.atomic():
        try:
            ContaReceber.objects.select_for_update().get(
                contrato=contrato
            ).atualizar_situacao(novo_valor_total=novo_total)
        except ContaReceber.DoesNotExist:
            pass


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
        valor=instance.caucao_valor,
        data_competencia=timezone.now().date(),
        data_pagamento=timezone.now().date(),
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
