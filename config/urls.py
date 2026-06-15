from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.views.defaults import page_not_found, server_error

handler404 = 'django.views.defaults.page_not_found'
handler500 = 'django.views.defaults.server_error'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path('', include('apps.core.urls')),
    path('frota/', include(('apps.fleet.urls', 'frota'))),
    path('clientes/', include(('apps.customers.urls', 'clientes'))),
    path('contratos/', include(('apps.contracts.urls', 'contratos'))),
    path('financeiro/', include(('apps.financeiro.urls', 'financeiro'))),
    path('manutencao/', include(('apps.manutencao.urls', 'manutencao'))),
    path('investidores/', include(('apps.investidores.urls', 'investidores'))),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
