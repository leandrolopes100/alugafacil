from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0006_configuracaolocadora_custo_reposicao_combustivel'),
    ]

    operations = [
        migrations.AddField(
            model_name='despesaoperacional',
            name='debito_automatico',
            field=models.BooleanField(
                default=False,
                verbose_name='Débito automático',
                help_text=(
                    'Marque se as parcelas são debitadas automaticamente (ex: cartão de crédito). '
                    'O sistema as confirmará na data de vencimento sem ação manual.'
                ),
            ),
        ),
    ]
