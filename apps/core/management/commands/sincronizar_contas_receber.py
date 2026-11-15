from django.core.management.base import BaseCommand

from apps.financeiro.models import ContaReceber


class Command(BaseCommand):
    help = 'Recalcula total, pago e situação de todas as Contas a Receber a partir dos contratos.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--simular', action='store_true',
            help='Só mostra o que mudaria, sem gravar.',
        )

    def handle(self, *args, **opcoes):
        from django.db import transaction
        from apps.contracts.models import Contrato

        alteradas = 0
        for conta in ContaReceber.objects.exclude(situacao='cancelado').select_related('contrato'):
            contrato = Contrato.objects.get(pk=conta.contrato_id)
            novo_total, novo_pago = contrato.total_geral, contrato.total_pago
            if (novo_total, novo_pago) == (conta.valor_total, conta.valor_pago):
                continue
            alteradas += 1
            self.stdout.write(
                f'{contrato.numero}: total {conta.valor_total} → {novo_total} | '
                f'pago {conta.valor_pago} → {novo_pago}'
            )
            if not opcoes['simular']:
                with transaction.atomic():
                    ContaReceber.sincronizar(conta.contrato_id)

        acao = 'mudariam' if opcoes['simular'] else 'recalculadas'
        self.stdout.write(self.style.SUCCESS(f'{alteradas} conta(s) {acao}.'))
