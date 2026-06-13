import re
from django.core.exceptions import ValidationError


def _so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def validar_cpf(valor):
    """Valida CPF com verificacao de digito verificador."""
    cpf = _so_digitos(valor)
    if len(cpf) != 11 or len(set(cpf)) == 1:
        raise ValidationError('CPF invalido.')
    for i in range(9, 11):
        soma = sum(int(cpf[j]) * (i + 1 - j) for j in range(i))
        digito = (soma * 10 % 11) % 10
        if int(cpf[i]) != digito:
            raise ValidationError('CPF invalido.')


def validar_cnpj(valor):
    """Valida CNPJ com verificacao de digito verificador."""
    cnpj = _so_digitos(valor)
    if len(cnpj) != 14 or len(set(cnpj)) == 1:
        raise ValidationError('CNPJ invalido.')
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(cnpj[i]) * pesos[i] for i in range(pos))
        digito = 0 if soma % 11 < 2 else 11 - soma % 11
        if int(cnpj[pos]) != digito:
            raise ValidationError('CNPJ invalido.')


def validar_placa(valor):
    """Valida placa no padrao antigo (ABC1234) ou Mercosul (ABC1D23)."""
    placa = re.sub(r'[-\s]', '', str(valor or '')).upper()
    padrao_antigo = re.compile(r'^[A-Z]{3}\d{4}$')
    padrao_mercosul = re.compile(r'^[A-Z]{3}\d[A-Z]\d{2}$')
    if not (padrao_antigo.match(placa) or padrao_mercosul.match(placa)):
        raise ValidationError(
            'Placa invalida. Use o padrao antigo (ABC-1234) ou Mercosul (ABC1D23).'
        )
