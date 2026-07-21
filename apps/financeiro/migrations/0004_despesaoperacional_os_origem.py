import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0003_configuracaolocadora_alter_multatransito_options_and_more'),
        ('manutencao', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='despesaoperacional',
            name='os_origem',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='despesa_gerada',
                to='manutencao.ordemmanutencao',
                verbose_name='OS de origem',
            ),
        ),
    ]
