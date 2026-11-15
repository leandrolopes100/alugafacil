from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F, OuterRef, Subquery

from apps.financeiro.models import DespesaOperacional, ParcelaDespesa


class Command(BaseCommand):
    help = (
        'Lista as parcelas de despesa com débito automático pagas depois do vencimento '
        '(baixa automática antiga que gravava a data do dia). Só altera com --aplicar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Grava a data do vencimento como data de pagamento. Faça backup antes.',
        )

    def handle(self, *args, **opcoes):
        parcelas = list(DespesaOperacional.parcelas_auto_pagas_fora_do_vencimento())
        if not parcelas:
            self.stdout.write(self.style.SUCCESS('Nenhuma parcela para corrigir.'))
            return

        self.stdout.write(self.style.WARNING(f'{len(parcelas)} parcela(s) com data de pagamento depois do vencimento:'))
        for p in parcelas:
            forma = '' if p.forma_pagamento else f' | forma: (vazia) -> {p.despesa.forma_pagamento or "(vazia)"}'
            self.stdout.write(
                f'    #{p.pk} {p.despesa.descricao[:40]} — parcela {p.numero}: '
                f'vencimento {p.data_vencimento:%d/%m/%Y}, pago em {p.data_pagamento:%d/%m/%Y} '
                f'-> {p.data_vencimento:%d/%m/%Y}{forma}'
            )

        if not opcoes['aplicar']:
            self.stdout.write(self.style.WARNING(
                '\nNada foi alterado. Confira a lista (pagamentos manuais em atraso também aparecem aqui) '
                'e rode de novo com --aplicar depois do backup.'
            ))
            return

        ids = [p.pk for p in parcelas]
        forma_da_despesa = DespesaOperacional.objects.filter(pk=OuterRef('despesa_id')).values('forma_pagamento')[:1]
        with transaction.atomic():
            ParcelaDespesa.objects.filter(pk__in=ids).update(data_pagamento=F('data_vencimento'))
            ParcelaDespesa.objects.filter(pk__in=ids, forma_pagamento='').update(forma_pagamento=Subquery(forma_da_despesa))
        self.stdout.write(self.style.SUCCESS(f'{len(ids)} parcela(s) corrigida(s).'))
