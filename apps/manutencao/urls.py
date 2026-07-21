from django.urls import path
from . import views

app_name = 'manutencao'

urlpatterns = [
    path('', views.ManutencaoListView.as_view(), name='lista'),
    path('nova/', views.ManutencaoCreateView.as_view(), name='nova'),
    path('<int:pk>/', views.ManutencaoDetailView.as_view(), name='detalhe'),
    path('<int:pk>/editar/', views.ManutencaoUpdateView.as_view(), name='editar'),
    path('<int:pk>/status/', views.ManutencaoAlterarStatusView.as_view(), name='alterar-status'),
    path('alertas/', views.AlertaListView.as_view(), name='alertas'),
    path('alertas/novo/', views.AlertaCreateView.as_view(), name='alerta-novo'),
]
