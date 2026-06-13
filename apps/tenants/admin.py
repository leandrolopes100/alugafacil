from django.contrib import admin
from django_tenants.admin import TenantAdminMixin
from .models import TenantCompany, Domain


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 1


@admin.register(TenantCompany)
class TenantCompanyAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'plan', 'is_active', 'created_at')
    list_filter = ('plan', 'is_active')
    search_fields = ('name', 'cnpj', 'email')
    inlines = [DomainInline]
