from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0007_contrato_sequencia_ano'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parcelacontrato',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('caucao', 'Caucao'),
                    ('semanal', 'Semanal'),
                    ('mensal', 'Mensal'),
                    ('acerto', 'Acerto Final'),
                ],
                default='semanal',
                max_length=10,
                verbose_name='Tipo',
            ),
        ),
    ]
