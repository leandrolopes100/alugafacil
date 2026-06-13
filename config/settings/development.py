from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

# Mapeia IPs e aliases locais para o hostname do tenant dev
TENANT_HOSTNAME_MAP = {
    '127.0.0.1': 'localhost',
    '0.0.0.0':   'localhost',
}
