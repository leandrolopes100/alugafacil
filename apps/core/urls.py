from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),

    # Hub de relatórios
    path('relatorios/', views.RelatoriosIndexView.as_view(), name='relatorios'),

    # Relatórios individuais
    path('relatorios/contratos/', views.RelatorioContratosView.as_view(), name='relatorio-contratos'),
    path('relatorios/frota/', views.RelatorioFrotaView.as_view(), name='relatorio-frota'),
    path('relatorios/dre/', views.RelatorioFinanceiroView.as_view(), name='relatorio-dre'),
    path('relatorios/clientes/', views.RelatorioClientesView.as_view(), name='relatorio-clientes'),
    path('relatorios/inadimplencia/', views.RelatorioInadimplenciaView.as_view(), name='relatorio-inadimplencia'),

    # Exportação CSV
    path('relatorios/exportar/<str:tipo>/', views.ExportarCSVView.as_view(), name='relatorio-exportar'),

    # Manual do usuário
    path('manual/', views.ManualUsuarioPDFView.as_view(), name='manual'),

    # Busca global
    path('busca/', views.BuscaGlobalView.as_view(), name='busca'),
]
