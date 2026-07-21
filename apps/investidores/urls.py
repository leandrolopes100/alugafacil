from django.urls import path
from . import views

app_name = 'investidores'

urlpatterns = [
    # Investidores
    path('', views.InvestidorListView.as_view(), name='lista'),
    path('novo/', views.InvestidorCreateView.as_view(), name='novo'),
    path('<int:pk>/', views.InvestidorDetalheView.as_view(), name='detalhe'),
    path('<int:pk>/editar/', views.InvestidorEditarView.as_view(), name='editar'),
    path('<int:pk>/vincular/', views.VincularVeiculoView.as_view(), name='vincular'),

    # Vínculos
    path('vinculo/<int:vi_pk>/desvincular/', views.DesvincularVeiculoView.as_view(), name='desvincular'),
    path('vinculo/<int:vi_pk>/cobrar/', views.GerarCobrancaView.as_view(), name='gerar-cobranca'),

    # Cobranças
    path('cobrancas/', views.CobrancaListView.as_view(), name='cobrancas'),
    path('cobrancas/gerar-lote/', views.GerarCobrancaLoteView.as_view(), name='gerar-lote'),
    path('cobrancas/<int:pk>/pagar/', views.PagarCobrancaView.as_view(), name='pagar-cobranca'),
    path('cobrancas/<int:pk>/cancelar/', views.CancelarCobrancaView.as_view(), name='cancelar-cobranca'),
]
