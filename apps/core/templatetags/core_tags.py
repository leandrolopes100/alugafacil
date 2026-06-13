from django import template

register = template.Library()


@register.filter
def get_item(dicionario, chave):
    """Acessa um dicionário pelo key em templates: {{ dict|get_item:key }}"""
    if isinstance(dicionario, dict):
        return dicionario.get(chave, 0)
    return 0
