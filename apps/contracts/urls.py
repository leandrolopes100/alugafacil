from django.urls import path
from . import views

app_name = 'contratos'

urlpatterns = [
    # Reservas
    path('reservas/', views.ReservaListView.as_view(), name='reserva-lista'),
    path('reservas/nova/', views.ReservaCreateView.as_view(), name='reserva-nova'),
    path('reservas/<int:pk>/', views.ReservaDetailView.as_view(), name='reserva-detalhe'),
    path('reservas/<int:pk>/editar/', views.ReservaUpdateView.as_view(), name='reserva-editar'),
    path('reservas/<int:pk>/excluir/', views.ReservaDeleteView.as_view(), name='reserva-excluir'),
    path('reservas/<int:pk>/confirmar/', views.ReservaConfirmarView.as_view(), name='reserva-confirmar'),
    path('reservas/<int:pk>/cancelar/', views.ReservaCancelarView.as_view(), name='reserva-cancelar'),
    path('reservas/<int:pk>/no-show/', views.ReservaNoShowView.as_view(), name='reserva-no-show'),
    path('reservas/<int:pk>/converter/', views.ReservaConverterView.as_view(), name='reserva-converter'),

    # Contratos
    path('', views.ContratoListView.as_view(), name='lista'),
    path('novo/', views.ContratoCreateView.as_view(), name='novo'),
    path('<int:pk>/', views.ContratoDetailView.as_view(), name='detalhe'),
    path('<int:pk>/checkout/', views.ContratoCheckoutView.as_view(), name='checkout'),
    path('<int:pk>/checkin/', views.ContratoCheckinView.as_view(), name='checkin'),
    path('<int:pk>/encerrar/', views.ContratoEncerrarView.as_view(), name='encerrar'),
    path('<int:pk>/cancelar/', views.ContratoCancelarView.as_view(), name='cancelar'),
    path('<int:pk>/reverter-checkin/', views.ContratoReverterCheckinView.as_view(), name='reverter-checkin'),
    path('<int:pk>/pdf/', views.ContratoPDFView.as_view(), name='pdf'),
    path('<int:pk>/adicionais/', views.ContratoAdicionaisView.as_view(), name='adicionais'),
    path('<int:pk>/avarias/', views.ContratoAvariasView.as_view(), name='avarias'),
    path('<int:pk>/pagamentos/', views.ContratoPagamentosView.as_view(), name='pagamentos'),
    path('<int:pk>/prorrogar/', views.ContratoProrrogarView.as_view(), name='prorrogar'),
    path('<int:pk>/parcelas/<int:parcela_pk>/pagar/', views.PagarParcelaView.as_view(), name='parcela-pagar'),
    path('<int:pk>/avarias/<int:avaria_pk>/pagar/', views.ContratoAvariaMarcarPagaView.as_view(), name='avaria-pagar'),

    # Assinatura digital (URL pública)
    path('assinar/<uuid:token>/', views.AssinaturaView.as_view(), name='assinar'),
]
