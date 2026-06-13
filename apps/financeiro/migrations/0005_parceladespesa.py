import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0004_despesaoperacional_os_origem'),
    ]

    operations = [
        # Novos campos em DespesaOperacional
        migrations.AddField(
            model_name='despesaoperacional',
            name='parcelado',
            field=models.BooleanField(default=False, verbose_name='Parcelado'),
        ),
        migrations.AddField(
            model_name='despesaoperacional',
            name='numero_parcelas',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, verbose_name='Numero de parcelas'
            ),
        ),
        migrations.AddField(
            model_name='despesaoperacional',
            name='forma_pagamento',
            field=models.CharField(
                blank=True,
                choices=[
                    ('cartao_credito', 'Cartao de Credito'),
                    ('cartao_debito', 'Cartao de Debito'),
                    ('boleto', 'Boleto Bancario'),
                    ('cheque', 'Cheque'),
                    ('pix', 'PIX'),
                    ('transferencia', 'Transferencia Bancaria'),
                    ('dinheiro', 'Dinheiro'),
                ],
                max_length=20,
                verbose_name='Forma de pagamento',
            ),
        ),
        migrations.AddIndex(
            model_name='despesaoperacional',
            index=models.Index(fields=['parcelado'], name='financeiro_despesa_parcelado_idx'),
        ),
        # Novo model ParcelaDespesa
        migrations.CreateModel(
            name='ParcelaDespesa',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numero', models.PositiveSmallIntegerField(verbose_name='No da Parcela')),
                ('valor', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Valor (R$)')),
                ('data_vencimento', models.DateField(verbose_name='Vencimento')),
                ('situacao', models.CharField(
                    choices=[('pendente', 'Pendente'), ('pago', 'Pago'), ('em_atraso', 'Em Atraso')],
                    default='pendente', max_length=10, verbose_name='Situacao',
                )),
                ('data_pagamento', models.DateField(blank=True, null=True, verbose_name='Pago em')),
                ('observacoes', models.TextField(blank=True, verbose_name='Observacoes')),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('despesa', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='parcelas',
                    to='financeiro.despesaoperacional',
                    verbose_name='Despesa',
                )),
            ],
            options={
                'verbose_name': 'Parcela de Despesa',
                'verbose_name_plural': 'Parcelas de Despesa',
                'ordering': ['despesa', 'numero'],
            },
        ),
        migrations.AddIndex(
            model_name='parceladespesa',
            index=models.Index(fields=['situacao'], name='financeiro_parcela_situacao_idx'),
        ),
        migrations.AddIndex(
            model_name='parceladespesa',
            index=models.Index(fields=['data_vencimento'], name='financeiro_parcela_vencimento_idx'),
        ),
        migrations.AddIndex(
            model_name='parceladespesa',
            index=models.Index(fields=['despesa', 'situacao'], name='financeiro_parcela_despesa_sit_idx'),
        ),
    ]
