import io
import re

from django.core.files.base import ContentFile
from PIL import Image


def comprime_imagem(field, max_dim=1920, quality=80):
    """
    Comprime e redimensiona um ImageField antes de ser gravado no storage.

    Deve ser chamado no save() do model somente quando o campo ainda não foi
    persistido (_committed is False), ou seja, quando há um novo upload.

    Converte qualquer modo para RGB (JPEG não suporta transparência), limita
    as dimensões a max_dim px e salva com qualidade `quality`. Remove EXIF
    automaticamente (Pillow não copia metadados ao salvar).

    Retorna um ContentFile pronto para ser atribuído ao campo, ou None se a
    imagem não puder ser processada.
    """
    if not field:
        return None
    try:
        img = Image.open(field)
        img.load()
    except Exception:
        return None

    if img.mode not in ('RGB',):
        img = img.convert('RGB')

    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality, optimize=True)
    buf.seek(0)

    nome = getattr(field, 'name', None) or 'foto'
    nome_base = nome.rsplit('.', 1)[0] if '.' in nome else nome
    return ContentFile(buf.read(), name=f'{nome_base}.jpg')


def numero_whatsapp(numero):
    """Converte um celular brasileiro para o formato do wa.me (55 + DDD + número).
    Retorna None quando o número não é um celular válido."""
    digitos = re.sub(r'\D', '', numero or '')
    if len(digitos) in (12, 13) and digitos.startswith('55'):
        digitos = digitos[2:]
    if len(digitos) == 11 and digitos[2] == '9':
        return f'55{digitos}'
    if len(digitos) == 10 and digitos[2] in '6789':
        return f'55{digitos[:2]}9{digitos[2:]}'
    return None


ANO_MINIMO = 2000


def ler_inteiro(valor, padrao, minimo, maximo):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return numero if minimo <= numero <= maximo else padrao


def ler_ano_mes(parametros, hoje, mes_padrao):
    ano = ler_inteiro(parametros.get('ano'), hoje.year, ANO_MINIMO, hoje.year + 1)
    mes_bruto = parametros.get('mes')
    if mes_bruto is None:
        return ano, mes_padrao
    if mes_bruto == '':
        return ano, 0
    return ano, ler_inteiro(mes_bruto, mes_padrao, 1, 12)
