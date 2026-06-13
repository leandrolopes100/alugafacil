from django.contrib import admin
from .models import Cliente, CNHCliente


class CNHClienteInline(admin.TabularInline):
    model = CNHCliente
    extra = 1
    fields = ('numero', 'categoria', 'validade', 'principal')


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nome_exibicao', 'tipo', 'documento', 'telefone', 'cidade', 'situacao')
    list_filter = ('tipo', 'situacao', 'estado')
    search_fields = ('nome', 'razao_social', 'cpf', 'cnpj', 'email', 'telefone')
    inlines = [CNHClienteInline]


@admin.register(CNHCliente)
class CNHClienteAdmin(admin.ModelAdmin):
    list_display = ('numero', 'cliente', 'categoria', 'validade', 'vencida')
    list_filter = ('categoria',)
    search_fields = ('numero', 'cliente__nome')
