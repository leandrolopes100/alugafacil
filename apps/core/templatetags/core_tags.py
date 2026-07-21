from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def get_item(dicionario, chave):
    """Acessa um dicionário pelo key em templates: {{ dict|get_item:key }}"""
    if isinstance(dicionario, dict):
        return dicionario.get(chave, 0)
    return 0


@register.filter
def brl(value):
    """Formata valor como moeda brasileira: R$ X.XXX,XX"""
    try:
        v = Decimal(str(value or 0))
        en = f"{v:,.2f}"  # "1,599.92"
        br = en.replace(',', 'X').replace('.', ',').replace('X', '.')
        return f"R$ {br}"
    except (InvalidOperation, TypeError, ValueError):
        return "R$ 0,00"
