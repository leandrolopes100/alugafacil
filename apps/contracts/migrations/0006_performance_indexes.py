from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0005_alter_contrato_situacao'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='reserva',
            index=models.Index(fields=['situacao'], name='reserva_situacao_idx'),
        ),
        migrations.AddIndex(
            model_name='reserva',
            index=models.Index(fields=['data_retirada'], name='reserva_data_retirada_idx'),
        ),
        migrations.AddIndex(
            model_name='pagamentocontrato',
            index=models.Index(fields=['data_pagamento'], name='pagamento_data_idx'),
        ),
        migrations.AddIndex(
            model_name='pagamentocontrato',
            index=models.Index(fields=['contrato', 'tipo'], name='pagamento_contrato_tipo_idx'),
        ),
        migrations.AddIndex(
            model_name='fotocontrato',
            index=models.Index(fields=['contrato', 'momento'], name='foto_contrato_momento_idx'),
        ),
        migrations.AddIndex(
            model_name='avariacontrato',
            index=models.Index(fields=['situacao'], name='avaria_situacao_idx'),
        ),
    ]
