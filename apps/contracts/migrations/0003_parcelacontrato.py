from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0002_pagamentocontrato'),
    ]

    operations = [
        migrations.CreateModel(
            name='ParcelaContrato',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numero', models.PositiveSmallIntegerField(verbose_name='Nº')),
                ('tipo', models.CharField(choices=[('caucao', 'Caução'), ('semanal', 'Semanal')], default='semanal', max_length=10, verbose_name='Tipo')),
                ('data_vencimento', models.DateField(verbose_name='Vencimento')),
                ('valor', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Valor (R$)')),
                ('situacao', models.CharField(choices=[('pendente', 'Pendente'), ('pago', 'Pago'), ('em_atraso', 'Em Atraso'), ('cancelada', 'Cancelada')], default='pendente', max_length=15, verbose_name='Situação')),
                ('data_pagamento', models.DateTimeField(blank=True, null=True, verbose_name='Pago em')),
                ('forma_pagamento', models.CharField(blank=True, choices=[('dinheiro', 'Dinheiro'), ('pix', 'PIX'), ('cartao_credito', 'Cartão de Crédito'), ('cartao_debito', 'Cartão de Débito'), ('transferencia', 'Transferência Bancária'), ('cheque', 'Cheque')], max_length=20, null=True, verbose_name='Forma')),
                ('observacoes', models.TextField(blank=True, verbose_name='Observações')),
                ('origem', models.CharField(choices=[('original', 'Contrato Original'), ('prorrogacao', 'Prorrogação')], default='original', max_length=15, verbose_name='Origem')),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('contrato', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parcelas', to='contracts.contrato', verbose_name='Contrato')),
            ],
            options={
                'verbose_name': 'Parcela',
                'verbose_name_plural': 'Parcelas',
                'ordering': ['data_vencimento', 'numero'],
            },
        ),
    ]
