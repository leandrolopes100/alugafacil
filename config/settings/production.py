import os
from pathlib import Path
from .base import *
from decouple import config

DEBUG = False

ALLOWED_HOSTS = [h.strip() for h in config('ALLOWED_HOSTS', default='localhost').split(',')]

SECRET_KEY = config('SECRET_KEY')

# ─── Segurança ────────────────────────────────────────────────────────────────
# SECURE_SSL_REDIRECT omitido: PythonAnywhere termina SSL no proxy próprio.
# SECURE_PROXY_SSL_HEADER garante que Django reconheça a conexão como HTTPS.
# W008 silenciado para evitar falso-positivo no manage.py check --deploy.
SILENCED_SYSTEM_CHECKS = ['security.W008']

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ─── Banco de dados (SQLite — herda de base.py) ───────────────────────────────
# Arquivo: /home/<usuario>/alugafacil/db.sqlite3

# ─── Arquivos estáticos e mídia ───────────────────────────────────────────────

STATIC_URL  = '/static/'
STATIC_ROOT = config('STATIC_ROOT', default=str(BASE_DIR / 'staticfiles'))
# Herda STATICFILES_DIRS = [BASE_DIR / 'static'] de base.py para que
# collectstatic inclua os arquivos globais do projeto (CSS, JS, imagens).

MEDIA_URL  = '/media/'
MEDIA_ROOT = config('MEDIA_ROOT', default=str(BASE_DIR / 'media'))

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ─── Email ───────────────────────────────────────────────────────────────────

EMAIL_BACKEND      = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST         = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT         = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS      = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER    = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@alugafacil.com.br')

# ─── Logging ─────────────────────────────────────────────────────────────────
# Cria o diretório de logs automaticamente se não existir (ex: após git clone).

_LOG_FILE = config('LOG_FILE', default=str(BASE_DIR / 'logs' / 'django.log'))
Path(_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': _LOG_FILE,
            'maxBytes': 1024 * 1024 * 10,
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'apps': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
