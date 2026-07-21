# Ponto de entrada para o pa_autoconfigure_django do PythonAnywhere.
# O script deles exige um settings.py na raiz do projeto.
# Toda a configuração real está em config/settings/production.py.
from config.settings.production import *

# Overrides especificos de uma instancia (ex.: servidor de teste sem Postgres) --
# local_settings.py fica no .gitignore, entao isso nunca conflita com git pull.
try:
    from local_settings import *
except ImportError:
    pass