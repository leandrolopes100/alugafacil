import io

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
