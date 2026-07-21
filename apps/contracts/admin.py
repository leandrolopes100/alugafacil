from django.contrib import admin
from .models import (
    Reserva, Contrato, AdicionalContrato, FotoContrato,
    AvariaContrato, PagamentoContrato, ParcelaContrato, HistoricoContrato,
)


class AdicionalContratoInline(admin.TabularInline):
    model = AdicionalContrato
    extra = 0


class AvariaContratoInline(admin.TabularInline):
    model = AvariaContrato
    extra = 0


class PagamentoContratoInline(admin.TabularInline):
    model = PagamentoContrato
    extra = 0
    fields = ('forma_pagamento', 'tipo', 'valor', 'data_pagamento')
    readonly_fields = ('registrado_por',)


class ParcelaContratoInline(admin.TabularInline):
    model = ParcelaContrato
    extra = 0
    fields = ('numero', 'tipo', 'data_vencimento', 'valor', 'situacao', 'data_pagamento')


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ('pk', 'cliente', 'grupo_veiculo', 'data_retirada', 'data_devolucao', 'canal', 'situacao')
    list_filter = ('situacao', 'canal')
    search_fields = ('cliente__nome', 'cliente__cpf')
    date_hierarchy = 'data_retirada'


@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ('numero', 'cliente', 'veiculo', 'data_saida', 'data_devolucao_prevista', 'situacao', 'assinado')
    list_filter = ('situacao', 'caucao_situacao')
    search_fields = ('numero', 'cliente__nome', 'veiculo__placa')
    date_hierarchy = 'criado_em'
    inlines = [AdicionalContratoInline, AvariaContratoInline, PagamentoContratoInline, ParcelaContratoInline]
    readonly_fields = ('numero', 'token_assinatura', 'encerrado_por', 'encerrado_em')


@admin.register(PagamentoContrato)
class PagamentoContratoAdmin(admin.ModelAdmin):
    list_display = ('contrato', 'forma_pagamento', 'tipo', 'valor', 'data_pagamento', 'registrado_por')
    list_filter = ('forma_pagamento', 'tipo')
    search_fields = ('contrato__numero', 'contrato__cliente__nome')
    date_hierarchy = 'data_pagamento'
    readonly_fields = ('registrado_por',)


@admin.register(ParcelaContrato)
class ParcelaContratoAdmin(admin.ModelAdmin):
    list_display = ('contrato', 'numero', 'tipo', 'data_vencimento', 'valor', 'situacao', 'origem')
    list_filter = ('situacao', 'tipo', 'origem')
    search_fields = ('contrato__numero', 'contrato__cliente__nome')
    date_hierarchy = 'data_vencimento'


@admin.register(FotoContrato)
class FotoContratoAdmin(admin.ModelAdmin):
    list_display = ('contrato', 'momento', 'posicao', 'criado_em')
    list_filter = ('momento', 'posicao')
    search_fields = ('contrato__numero',)


@admin.register(HistoricoContrato)
class HistoricoContratoAdmin(admin.ModelAdmin):
    list_display = ('contrato', 'acao', 'situacao_anterior', 'situacao_nova', 'usuario', 'criado_em')
    list_filter = ('acao',)
    search_fields = ('contrato__numero', 'contrato__cliente__nome')
    date_hierarchy = 'criado_em'
    readonly_fields = ('usuario', 'criado_em')
