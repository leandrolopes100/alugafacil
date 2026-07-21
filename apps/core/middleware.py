from django.conf import settings
from django.template.loader import render_to_string
from django.http import HttpResponse


class MaintenanceModeMiddleware:
    """Retorna 503 para todas as requisições quando MAINTENANCE_MODE=True em settings."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, 'MAINTENANCE_MODE', False):
            html = render_to_string('503.html', request=request)
            return HttpResponse(html, status=503)
        return self.get_response(request)
