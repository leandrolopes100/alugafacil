from django.urls import path
from . import views

app_name = 'frota'

urlpatterns = [
    path('', views.VeiculoListView.as_view(), name='lista'),
    path('novo/', views.VeiculoCreateView.as_view(), name='novo'),
    path('<int:pk>/', views.VeiculoDetailView.as_view(), name='detalhe'),
    path('<int:pk>/editar/', views.VeiculoUpdateView.as_view(), name='editar'),
    path('<int:pk>/excluir/', views.VeiculoDeleteView.as_view(), name='excluir'),
    path('<int:pk>/fotos/', views.VeiculoFotosView.as_view(), name='fotos'),
    path('<int:pk>/fotos/<int:foto_pk>/excluir/', views.VeiculoFotoExcluirView.as_view(), name='foto-excluir'),
    path('<int:pk>/documentos/', views.VeiculoDocumentosView.as_view(), name='documentos'),
    path('<int:pk>/documentos/<int:doc_pk>/excluir/', views.VeiculoDocumentoExcluirView.as_view(), name='documento-excluir'),
    path('grupos/', views.GrupoListView.as_view(), name='grupos'),
    path('grupos/novo/', views.GrupoCreateView.as_view(), name='grupo-novo'),
    path('grupos/<int:pk>/editar/', views.GrupoUpdateView.as_view(), name='grupo-editar'),
    path('disponibilidade/', views.DisponibilidadeView.as_view(), name='disponibilidade'),
    path('tarifas/', views.VeiculoTarifasView.as_view(), name='tarifas'),
    path('categorias/', views.CategoriaListView.as_view(), name='categorias'),
    path('categorias/nova/', views.CategoriaCreateView.as_view(), name='categoria-nova'),
    path('categorias/<int:pk>/editar/', views.CategoriaUpdateView.as_view(), name='categoria-editar'),
]
