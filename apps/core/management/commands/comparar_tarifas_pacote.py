from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand

from apps.contracts.models import Contrato
from apps.fleet.tarifas import _brl, valor_base_ciclo


class Command(BaseCommand):
    help = (
        'Compara contratos criados antes da cobrança por pacote com o que a regra nova cobraria. '
        'Não altera nada.'
    )

    def handle(self, *args, **opcoes):
        contratos = (
            Contrato.objects.filter(
                valor_semanal__isnull=True, valor_mensal__isnull=True,
                situacao__in=['aberto', 'ativo', 'aguardando_devolucao'],
                veiculo__grupo__isnull=False,
            )
            .select_related('cliente', 'veiculo__grupo')
            .order_by('numero')
        )
        diferenca_total = Decimal('0.00')
        encontrados = 0
        for contrato in contratos:
            grupo = contrato.veiculo.grupo
            pacote = grupo.mensal if contrato.forma_cobranca == 'mensal' else grupo.semanal
            if not pacote:
                continue
            adicionais_dia = contrato.diaria_adicionais
            linhas = []
            for parcela in contrato.parcelas.filter(
                situacao__in=['pendente', 'em_atraso'], tipo__in=['semanal', 'mensal'], dias__isnull=False,
            ).order_by('data_vencimento'):
                if parcela.tipo == 'mensal':
                    completo = (parcela.data_vencimento + relativedelta(months=1) - parcela.data_vencimento).days == parcela.dias
                else:
                    completo = parcela.dias == 7
                novo = valor_base_ciclo(contrato.diaria, parcela.dias, pacote, completo) + adicionais_dia * parcela.dias
                if novo != parcela.valor:
                    linhas.append((parcela, novo))
            if not linhas and contrato.situacao != 'aberto':
                continue
            encontrados += 1
            self.stdout.write(self.style.WARNING(
                f'\n{contrato.numero} — {contrato.cliente} — {contrato.veiculo.placa} '
                f'({grupo.nome}, {contrato.get_situacao_display()}, cobrança {contrato.get_forma_cobranca_display().lower()})'
            ))
            if contrato.situacao == 'aberto':
                self.stdout.write(
                    f'    Ainda não saiu: no check-out será cobrado pela diária ({_brl(contrato.diaria)}), '
                    f'não pelo pacote do grupo ({_brl(pacote)}). Para aplicar o pacote, cancele e crie o contrato de novo.'
                )
            for parcela, novo in linhas:
                diferenca = parcela.valor - novo
                diferenca_total += diferenca
                self.stdout.write(
                    f'    {parcela.rotulo} venc. {parcela.data_vencimento:%d/%m/%Y} ({parcela.dias} dias): '
                    f'hoje {_brl(parcela.valor)} → pela regra nova {_brl(novo)} (diferença {_brl(diferenca)})'
                )
        self.stdout.write('')
        if encontrados:
            self.stdout.write(self.style.WARNING(
                f'{encontrados} contrato(s) para revisar; diferença nas parcelas pendentes: {_brl(diferenca_total)}. '
                f'Nada foi alterado.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Nenhum contrato antigo ficaria diferente pela regra nova.'))
