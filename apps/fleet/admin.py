from django.contrib import admin
from .models import CategoriaVeiculo, GrupoVeiculo, Veiculo, FotoVeiculo, DocumentoVeiculo, HistoricoKmVeiculo


class FotoVeiculoInline(admin.TabularInline):
    model = FotoVeiculo
    extra = 1
    fields = ('imagem', 'posicao', 'principal')


class DocumentoVeiculoInline(admin.TabularInline):
    model = DocumentoVeiculo
    extra = 0
    fields = ('tipo', 'numero', 'data_validade')


@admin.register(CategoriaVeiculo)
class CategoriaVeiculoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ordem', 'ativo')
    list_editable = ('ordem', 'ativo')


@admin.register(GrupoVeiculo)
class GrupoVeiculoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'categoria', 'diaria', 'caucao', 'ativo')
    list_filter = ('categoria', 'ativo')
    search_fields = ('nome',)


@admin.register(Veiculo)
class VeiculoAdmin(admin.ModelAdmin):
    list_display = ('placa', 'marca', 'modelo', 'ano_modelo', 'grupo', 'situacao', 'km_atual')
    list_filter = ('situacao', 'grupo__categoria', 'combustivel')
    search_fields = ('placa', 'marca', 'modelo', 'chassi', 'renavam')
    inlines = [FotoVeiculoInline, DocumentoVeiculoInline]


@admin.register(DocumentoVeiculo)
class DocumentoVeiculoAdmin(admin.ModelAdmin):
    list_display = ('veiculo', 'tipo', 'numero', 'data_validade', 'vencido')
    list_filter = ('tipo',)
    search_fields = ('veiculo__placa', 'numero')


@admin.register(HistoricoKmVeiculo)
class HistoricoKmVeiculoAdmin(admin.ModelAdmin):
    list_display = ('veiculo', 'km', 'data', 'origem', 'contrato', 'registrado_por')
    list_filter = ('origem',)
    search_fields = ('veiculo__placa', 'contrato__numero')
    date_hierarchy = 'data'
    readonly_fields = ('contrato', 'registrado_por', 'criado_em')
