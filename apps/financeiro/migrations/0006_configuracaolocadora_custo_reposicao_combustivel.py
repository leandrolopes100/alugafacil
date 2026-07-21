from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0005_parceladespesa'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaolocadora',
            name='custo_reposicao_combustivel',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Valor cobrado por quarto de tanque faltante na devolucao. 0 = nao cobrar.',
                max_digits=8,
                verbose_name='Custo reposicao combustivel (R$/1/4 tanque)',
            ),
        ),
    ]
