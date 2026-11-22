from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

DIAS_SEMANA = 7
DIAS_MES_REFERENCIA = 30
DESCONTO_MAXIMO_SEM_CONFIRMACAO = Decimal('0.50')
CENTAVO = Decimal('0.01')


def _arredondar(valor):
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def diaria_pela_semana(semanal):
    return (Decimal(semanal) / DIAS_SEMANA).quantize(CENTAVO, rounding=ROUND_CEILING)


def valor_base_ciclo(diaria, dias, pacote=None, completo=False):
    por_dia = _arredondar(Decimal(diaria) * dias)
    if not pacote:
        return por_dia
    if completo:
        return _arredondar(pacote)
    return min(por_dia, _arredondar(pacote))


def desconto(pacote, diaria, dias):
    cheio = Decimal(diaria) * dias
    if not pacote or not cheio:
        return Decimal('0')
    return (cheio - Decimal(pacote)) / cheio


def conferir_tarifas(diaria, semanal=None, mensal=None):
    erros, avisos, infos = [], [], []
    if not diaria or diaria <= 0:
        return {'erros': erros, 'avisos': avisos, 'infos': infos}
    diaria = Decimal(diaria)
    sete_diarias = _arredondar(diaria * DIAS_SEMANA)
    trinta_diarias = _arredondar(diaria * DIAS_MES_REFERENCIA)

    if semanal:
        if semanal > sete_diarias:
            erros.append({
                'campo': 'semanal',
                'texto': f'O semanal ({_brl(semanal)}) é mais caro que 7 diárias ({_brl(sete_diarias)}). '
                         f'O cliente pagaria mais no pacote do que por dia.',
                'sugestao': sete_diarias,
            })
        elif desconto(semanal, diaria, DIAS_SEMANA) > DESCONTO_MAXIMO_SEM_CONFIRMACAO:
            avisos.append({
                'campo': 'semanal',
                'texto': f'O semanal ({_brl(semanal)}) dá {_pct(desconto(semanal, diaria, DIAS_SEMANA))} '
                         f'de desconto sobre 7 diárias ({_brl(sete_diarias)}). Confira se não houve erro de digitação.',
                'sugestao': None,
            })
    else:
        infos.append({
            'campo': 'semanal',
            'texto': f'Semanal em branco: a semana será cobrada pela diária, 7 × {_brl(diaria)} = {_brl(sete_diarias)}.',
            'sugestao': None,
        })

    if mensal:
        if mensal > trinta_diarias:
            erros.append({
                'campo': 'mensal',
                'texto': f'O mensal ({_brl(mensal)}) é mais caro que 30 diárias ({_brl(trinta_diarias)}). '
                         f'O cliente pagaria mais no pacote do que por dia.',
                'sugestao': trinta_diarias,
            })
        elif semanal and semanal <= sete_diarias and mensal > _arredondar(Decimal(semanal) * DIAS_MES_REFERENCIA / DIAS_SEMANA):
            limite = _arredondar(Decimal(semanal) * DIAS_MES_REFERENCIA / DIAS_SEMANA)
            erros.append({
                'campo': 'mensal',
                'texto': f'O mensal ({_brl(mensal)}) é mais caro que 30 dias pelo semanal ({_brl(limite)}). '
                         f'Ninguém escolheria o pacote mensal.',
                'sugestao': limite,
            })
        elif desconto(mensal, diaria, DIAS_MES_REFERENCIA) > DESCONTO_MAXIMO_SEM_CONFIRMACAO:
            avisos.append({
                'campo': 'mensal',
                'texto': f'O mensal ({_brl(mensal)}) dá {_pct(desconto(mensal, diaria, DIAS_MES_REFERENCIA))} '
                         f'de desconto sobre 30 diárias ({_brl(trinta_diarias)}). Confira se não houve erro de digitação.',
                'sugestao': None,
            })
    else:
        infos.append({
            'campo': 'mensal',
            'texto': f'Mensal em branco: o mês será cobrado pela diária, dias do mês × {_brl(diaria)} '
                     f'(30 dias = {_brl(trinta_diarias)}).',
            'sugestao': None,
        })
    return {'erros': erros, 'avisos': avisos, 'infos': infos}


def resumo_cobranca(diaria, semanal=None, mensal=None):
    diaria = Decimal(diaria)
    semana = valor_base_ciclo(diaria, DIAS_SEMANA, semanal, completo=True)
    mes = valor_base_ciclo(diaria, DIAS_MES_REFERENCIA, mensal, completo=True)
    return {
        'semana': semana,
        'semana_por_dia': _arredondar(semana / DIAS_SEMANA),
        'semana_desconto': desconto(semana, diaria, DIAS_SEMANA),
        'mes': mes,
        'mes_por_dia': _arredondar(mes / DIAS_MES_REFERENCIA),
        'mes_desconto': desconto(mes, diaria, DIAS_MES_REFERENCIA),
        'exemplo_semanal_10_dias': semana + valor_base_ciclo(diaria, 3, semanal),
        'exemplo_mensal_45_dias': mes + valor_base_ciclo(diaria, 15, mensal),
    }


def forma_cobranca_sugerida(grupo):
    if grupo and grupo.mensal and not grupo.semanal:
        return 'mensal'
    return 'semanal'


def _brl(valor):
    return 'R$ ' + f'{float(valor):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _pct(fracao):
    return f'{float(fracao) * 100:.0f}%'.replace('.', ',')
