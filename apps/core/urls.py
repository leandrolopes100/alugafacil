from django.urls import path
from . import views, views_acessos

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

    path('acessos/usuarios/', views_acessos.UsuarioListView.as_view(), name='usuarios'),
    path('acessos/usuarios/novo/', views_acessos.UsuarioCreateView.as_view(), name='usuario-novo'),
    path('acessos/usuarios/<int:pk>/editar/', views_acessos.UsuarioUpdateView.as_view(), name='usuario-editar'),
    path('acessos/usuarios/<int:pk>/senha/', views_acessos.UsuarioSenhaView.as_view(), name='usuario-senha'),
    path('acessos/perfis/', views_acessos.PerfilListView.as_view(), name='perfis'),
    path('acessos/perfis/novo/', views_acessos.PerfilCreateView.as_view(), name='perfil-novo'),
    path('acessos/perfis/<int:pk>/editar/', views_acessos.PerfilUpdateView.as_view(), name='perfil-editar'),
    path('acessos/perfis/<int:pk>/excluir/', views_acessos.PerfilDeleteView.as_view(), name='perfil-excluir'),

    # Busca global
    path('busca/', views.BuscaGlobalView.as_view(), name='busca'),
]
