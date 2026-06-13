from django.conf import settings
from django_tenants.middleware.main import TenantMainMiddleware


class TenantMiddleware(TenantMainMiddleware):
    """Estende TenantMainMiddleware adicionando mapeamento de hostnames.

    TENANT_HOSTNAME_MAP em settings permite que IPs ou aliases locais sejam
    redirecionados para um hostname registrado como Domain do tenant.
    Exemplo em development.py:
        TENANT_HOSTNAME_MAP = {'127.0.0.1': 'localhost'}
    """

    @staticmethod
    def hostname_from_request(request):
        host = request.get_host().split(':')[0].lower()
        mapping = getattr(settings, 'TENANT_HOSTNAME_MAP', {})
        return mapping.get(host, host)
