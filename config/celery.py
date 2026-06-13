import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')

app = Celery('alugafacil')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'marcar-parcelas-atrasadas-diario': {
        'task': 'apps.core.tasks.marcar_parcelas_atrasadas',
        'schedule': crontab(hour=6, minute=0),
    },
    'marcar-contas-vencidas-diario': {
        'task': 'apps.core.tasks.marcar_contas_vencidas',
        'schedule': crontab(hour=6, minute=10),
    },
    'alertar-documentos-vencendo-diario': {
        'task': 'apps.core.tasks.alertar_documentos_vencendo',
        'schedule': crontab(hour=7, minute=0),
    },
    'sincronizar-despesas-auto-diario': {
        'task': 'apps.core.tasks.sincronizar_despesas_auto',
        'schedule': crontab(hour=6, minute=5),
    },
    'marcar-parcelas-despesa-atrasadas-diario': {
        'task': 'apps.core.tasks.marcar_parcelas_despesa_atrasadas',
        'schedule': crontab(hour=6, minute=2),
    },
}
