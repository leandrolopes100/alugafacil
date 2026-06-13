from django.contrib import admin
from .models import (
    ConfiguracaoLocadora, DespesaOperacional, ParcelaDespesa,
    MultaTransito, ContaReceber,
)


class ParcelaDespesaInline(admin.TabularInline):
    model = ParcelaDespesa
    extra = 0
    fields = ('numero', 'valor', 'data_vencimento', 'situacao', 'data_pagamento')
    readonly_fields = ('numero',)


@admin.register(ConfiguracaoLocadora)
class ConfiguracaoLocadoraAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'percentual_multa_atraso', 'percentual_juros_diario', 'dias_carencia', 'custo_reposicao_combustivel')

    def has_add_permission(self, request):
        return not ConfiguracaoLocadora.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DespesaOperacional)
class DespesaAdmin(admin.ModelAdmin):
    list_display = ('descricao', 'categoria', 'valor', 'data_competencia', 'veiculo', 'pago')
    list_filter = ('categoria', 'parcelado')
    search_fields = ('descricao', 'veiculo__placa')
    date_hierarchy = 'data_competencia'
    inlines = [ParcelaDespesaInline]


@admin.register(ParcelaDespesa)
class ParcelaDespesaAdmin(admin.ModelAdmin):
    list_display = ('despesa', 'numero', 'valor', 'data_vencimento', 'situacao', 'data_pagamento')
    list_filter = ('situacao',)
    search_fields = ('despesa__descricao',)
    date_hierarchy = 'data_vencimento'


@admin.register(MultaTransito)
class MultaAdmin(admin.ModelAdmin):
    list_display = ('veiculo', 'data_infracao', 'valor', 'prazo_indicacao', 'situacao', 'prazo_critico')
    list_filter = ('situacao',)
    search_fields = ('veiculo__placa', 'numero_auto', 'condutor_nome')
    date_hierarchy = 'data_infracao'


@admin.register(ContaReceber)
class ContaReceberAdmin(admin.ModelAdmin):
    list_display = ('contrato', 'cliente', 'valor_total', 'valor_pago', 'data_vencimento', 'situacao')
    list_filter = ('situacao',)
    search_fields = ('contrato__numero', 'cliente__nome')
    date_hierarchy = 'data_vencimento'
    readonly_fields = ('valor_saldo', 'vencida', 'dias_em_atraso', 'criado_em', 'atualizado_em')
