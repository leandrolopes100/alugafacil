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


def validar_cep(valor):
    """Valida CEP: exatamente 8 digitos numericos."""
    cep = _so_digitos(valor)
    if len(cep) != 8:
        raise ValidationError('CEP invalido. Informe 8 digitos.')


def validar_chassi(valor):
    """Valida chassi VIN: 17 caracteres alfanumericos, sem I, O ou Q."""
    chassi = re.sub(r'[-\s]', '', str(valor or '')).upper()
    if len(chassi) != 17:
        raise ValidationError('Chassi invalido. Deve ter exatamente 17 caracteres.')
    if not re.match(r'^[A-HJ-NPR-Z0-9]{17}$', chassi):
        raise ValidationError('Chassi invalido. Use apenas letras (exceto I, O, Q) e numeros.')


def validar_renavam(valor):
    """Valida RENAVAM: 9 ou 11 digitos numericos."""
    renavam = _so_digitos(valor)
    if len(renavam) not in (9, 11):
        raise ValidationError('RENAVAM invalido. Deve ter 9 ou 11 digitos.')


def validar_cnh_numero(valor):
    """Valida numero de CNH: exatamente 11 digitos numericos."""
    numero = _so_digitos(valor)
    if len(numero) != 11:
        raise ValidationError('Numero de CNH invalido. Deve ter exatamente 11 digitos.')
