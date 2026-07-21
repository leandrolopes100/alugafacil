from django.contrib import admin
from .models import CobrancaGestao, Investidor, VeiculoInvestidor

admin.site.register(Investidor)
admin.site.register(VeiculoInvestidor)
admin.site.register(CobrancaGestao)
