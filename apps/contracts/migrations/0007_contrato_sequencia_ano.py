from django.db import migrations, models


def preencher_sequencia(apps, schema_editor):
    Contrato = apps.get_model('contracts', 'Contrato')
    for c in Contrato.objects.filter(sequencia_ano__isnull=True).order_by('numero'):
        try:
            c.sequencia_ano = int(c.numero.split('-')[-1])
            c.save(update_fields=['sequencia_ano'])
        except (ValueError, IndexError):
            pass


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0006_performance_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='contrato',
            name='sequencia_ano',
            field=models.PositiveIntegerField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name='Sequencia do ano',
            ),
        ),
        migrations.RunPython(preencher_sequencia, migrations.RunPython.noop),
    ]
