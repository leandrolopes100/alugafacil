from django.core.management.base import BaseCommand
from django.db import transaction

from apps.financeiro.models import MultaTransito


class Command(BaseCommand):
    help = (
        'Lista as multas sem contrato que a regra atual consegue ligar a um contrato '
        '(multas pagas ficam de fora). Só altera com --aplicar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Liga cada multa ao único contrato encontrado. Faça backup antes.',
        )

    def handle(self, *args, **opcoes):
        multas = MultaTransito.objects.filter(contrato__isnull=True).exclude(situacao='paga').select_related('veiculo')
        ligar, duvida, sem_contrato = [], [], 0
        for multa in multas:
            possiveis = list(MultaTransito.contratos_possiveis(multa.veiculo_id, multa.data_infracao))
            if len(possiveis) == 1:
                ligar.append((multa, possiveis[0]))
            elif possiveis:
                duvida.append((multa, possiveis))
            else:
                sem_contrato += 1

        self.stdout.write(self.style.WARNING(f'\n[{len(ligar)}] Multas que serão ligadas a um contrato'))
        for multa, contrato in ligar:
            self.stdout.write(
                f'    #{multa.pk} {multa.veiculo.placa} {multa.data_infracao:%d/%m/%Y} R$ {multa.valor} '
                f'-> {contrato.numero} ({contrato.cliente})'
            )
        self.stdout.write(self.style.WARNING(f'\n[{len(duvida)}] Multas com mais de um contrato possível (escolher na tela "Editar")'))
        for multa, possiveis in duvida:
            self.stdout.write(
                f'    #{multa.pk} {multa.veiculo.placa} {multa.data_infracao:%d/%m/%Y}: '
                f'{", ".join(c.numero for c in possiveis)}'
            )
        self.stdout.write(f'\n{sem_contrato} multa(s) sem nenhum contrato no dia da infração.')

        if not ligar:
            return
        if not opcoes['aplicar']:
            self.stdout.write(self.style.WARNING('\nNada foi alterado. Confira a lista e rode de novo com --aplicar depois do backup.'))
            return

        with transaction.atomic():
            for multa, contrato in ligar:
                MultaTransito.objects.filter(pk=multa.pk, contrato__isnull=True).update(contrato=contrato)
        self.stdout.write(self.style.SUCCESS(f'{len(ligar)} multa(s) ligada(s) ao contrato.'))
