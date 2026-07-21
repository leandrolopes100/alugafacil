from django.urls import path
from . import views

app_name = 'financeiro'

urlpatterns = [
    # Dashboard financeiro
    path('', views.IndexFinanceiroView.as_view(), name='index'),
    path('agenda/', views.AgendaCobrancasView.as_view(), name='agenda'),
    path('agenda-pagamentos/', views.AgendaPagamentosView.as_view(), name='agenda-pagamentos'),
    path('configuracao/', views.ConfiguracaoLocadoraView.as_view(), name='configuracao'),

    # Contas a Receber
    path('contas-receber/', views.ContaReceberListView.as_view(), name='contas-receber'),
    path('contas-receber/<int:pk>/', views.ContaReceberDetailView.as_view(), name='conta-receber-detalhe'),
    path('contas-receber/<int:pk>/receber/', views.ReceberPagamentoView.as_view(), name='receber'),
    path('contas-receber/<int:pk>/cancelar/', views.ContaReceberCancelarView.as_view(), name='conta-receber-cancelar'),

    # Despesas
    path('despesas/', views.DespesaListView.as_view(), name='despesas'),
    path('despesas/nova/', views.DespesaCreateView.as_view(), name='despesa-nova'),
    path('despesas/parcelas/pagar-lote/', views.ParcelaDespesaPagarLoteView.as_view(), name='parcelas-pagar-lote'),
    path('despesas/parcelas/estornar-lote/', views.ParcelaDespesaEstornarLoteView.as_view(), name='parcelas-estornar-lote'),
    path('despesas/<int:pk>/', views.DespesaDetailView.as_view(), name='despesa-detalhe'),
    path('despesas/<int:pk>/editar/', views.DespesaUpdateView.as_view(), name='despesa-editar'),
    path('despesas/<int:pk>/excluir/', views.DespesaDeleteView.as_view(), name='despesa-excluir'),
    path('despesas/<int:pk>/pagar/', views.DespesaMarcarPagoView.as_view(), name='despesa-pagar'),
    path('despesas/<int:pk>/despagar/', views.DespesaDesmarcarPagoView.as_view(), name='despesa-despagar'),
    path('despesas/parcelas/<int:pk>/pagar/', views.ParcelaDespesaPagarView.as_view(), name='parcela-despesa-pagar'),
    path('despesas/parcelas/<int:pk>/estornar/', views.ParcelaDespesaEstornarView.as_view(), name='parcela-despesa-estornar'),

    # Contas a Pagar
    path('contas-pagar/', views.ContasPagarListView.as_view(), name='contas-pagar'),

    # Fluxo de Caixa
    path('fluxo/', views.FluxoCaixaView.as_view(), name='fluxo'),

    # Multas
    path('multas/', views.MultaListView.as_view(), name='multas'),
    path('multas/nova/', views.MultaCreateView.as_view(), name='multa-nova'),
    path('multas/<int:pk>/', views.MultaDetailView.as_view(), name='multa-detalhe'),
    path('multas/<int:pk>/editar/', views.MultaUpdateView.as_view(), name='multa-editar'),
]
