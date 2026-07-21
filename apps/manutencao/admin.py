from django.contrib import admin
from .models import AlertaManutencao, OrdemManutencao


@admin.register(OrdemManutencao)
class OrdemManutencaoAdmin(admin.ModelAdmin):
    list_display = ('pk', 'veiculo', 'tipo', 'situacao', 'descricao', 'data_agendada', 'custo_total')
    list_filter = ('tipo', 'situacao')
    search_fields = ('veiculo__placa', 'descricao')


@admin.register(AlertaManutencao)
class AlertaManutencaoAdmin(admin.ModelAdmin):
    list_display = ('veiculo', 'descricao', 'tipo_alerta', 'km_proximo_servico', 'data_proximo_servico', 'ativo', 'vencido')
    list_filter = ('tipo_alerta', 'ativo')
    search_fields = ('veiculo__placa', 'descricao')
