import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP

import segno

TAMANHO_MAXIMO_NOME = 25
TAMANHO_MAXIMO_CIDADE = 15
TAMANHO_MAXIMO_TXID = 25
TXID_PADRAO = '***'


def _campo(identificador, valor):
    if len(valor) > 99:
        raise ValueError(f'Campo {identificador} excede 99 caracteres.')
    return f'{identificador}{len(valor):02d}{valor}'


def crc16(payload):
    crc = 0xFFFF
    for byte in payload.encode('utf-8'):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f'{crc:04X}'


def normalizar_texto(texto, tamanho):
    sem_acento = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode('ascii')
    limpo = re.sub(r'[^A-Za-z0-9 ]', '', sem_acento)
    return re.sub(r'\s+', ' ', limpo).strip()[:tamanho]


def normalizar_txid(texto):
    return re.sub(r'[^A-Za-z0-9]', '', texto or '')[:TAMANHO_MAXIMO_TXID] or TXID_PADRAO


def gerar_payload(chave, nome, cidade, valor=None, txid=None):
    conta = _campo('00', 'br.gov.bcb.pix') + _campo('01', chave)
    partes = [
        _campo('00', '01'),
        _campo('26', conta),
        _campo('52', '0000'),
        _campo('53', '986'),
    ]
    if valor is not None:
        valor = Decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if valor <= 0:
            raise ValueError('O valor do Pix deve ser maior que zero.')
        partes.append(_campo('54', f'{valor:.2f}'))
    partes += [
        _campo('58', 'BR'),
        _campo('59', normalizar_texto(nome, TAMANHO_MAXIMO_NOME)),
        _campo('60', normalizar_texto(cidade, TAMANHO_MAXIMO_CIDADE)),
        _campo('62', _campo('05', normalizar_txid(txid))),
    ]
    payload = ''.join(partes) + '6304'
    return payload + crc16(payload)


def qr_code_svg(payload, escala=5):
    return segno.make(payload, error='m').svg_inline(scale=escala, border=2)


def qr_code_png_data_uri(payload, escala=6):
    return segno.make(payload, error='m').png_data_uri(scale=escala, border=2)
