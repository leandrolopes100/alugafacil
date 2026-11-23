from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.financeiro.models import DespesaOperacional, MultaTransito


class Command(BaseCommand):
    help = (
        'Lista as multas marcadas como pagas antes de o sistema lançar a despesa automaticamente. '
        'Só lança com --aplicar e uma --data de pagamento informada.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--multa', type=int, action='append', default=[],
                            help='Limita a estas multas (pode repetir). Sem isso, considera todas as listadas.')
        parser.add_argument('--data', help='Data em que a locadora pagou, no formato AAAA-MM-DD.')
        parser.add_argument('--aplicar', action='store_true',
                            help='Lança a despesa de cada multa listada com a --data informada. Faça backup antes.')

    def handle(self, *args, **opcoes):
        multas = (
            MultaTransito.objects.filter(situacao='paga', despesa_gerada__isnull=True)
            .select_related('veiculo').order_by('data_infracao')
        )
        if opcoes['multa']:
            multas = multas.filter(pk__in=opcoes['multa'])
        multas = list(multas)

        self.stdout.write(self.style.WARNING(f'\n[{len(multas)}] Multas pagas sem despesa lançada'))
        for multa in multas:
            parecidas = DespesaOperacional.objects.filter(
                veiculo_id=multa.veiculo_id, valor=multa.valor, multa_origem__isnull=True,
            ).exclude(categoria='caucao')
            aviso = ''
            if parecidas.exists():
                ids = ', '.join(f'#{d.pk}' for d in parecidas[:5])
                aviso = f'  << ATENÇÃO: já existe despesa manual do mesmo valor neste veículo ({ids}); confira para não lançar em dobro'
            self.stdout.write(
                f'    #{multa.pk} {multa.veiculo.placa} infração {multa.data_infracao:%d/%m/%Y} R$ {multa.valor}{aviso}'
            )

        if not multas:
            return
        if not opcoes['aplicar']:
            self.stdout.write(self.style.WARNING(
                '\nNada foi alterado. Para lançar: --aplicar --data AAAA-MM-DD (use --multa N para escolher quais).'
            ))
            return
        if not opcoes['data']:
            raise CommandError('Informe --data com a data em que a locadora pagou. Ela define o mês do lançamento na DRE.')
        try:
            data_pagamento = date.fromisoformat(opcoes['data'])
        except ValueError:
            raise CommandError('Data inválida. Use AAAA-MM-DD.')

        with transaction.atomic():
            for multa in multas:
                multa.sincronizar_despesa(criar=True, data_pagamento=data_pagamento)
        self.stdout.write(self.style.SUCCESS(
            f'{len(multas)} despesa(s) lançada(s) com pagamento em {data_pagamento:%d/%m/%Y}.'
        ))
