from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0002_alter_cliente_cnpj_alter_cliente_cpf_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='cliente',
            index=GinIndex(
                fields=['nome'],
                name='cliente_nome_trgm_idx',
                opclasses=['gin_trgm_ops'],
            ),
        ),
    ]
