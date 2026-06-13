from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('fleet', '0002_historicokmveiculo_alter_categoriaveiculo_options_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='veiculo',
            index=GinIndex(
                fields=['placa'],
                name='veiculo_placa_trgm_idx',
                opclasses=['gin_trgm_ops'],
            ),
        ),
    ]
