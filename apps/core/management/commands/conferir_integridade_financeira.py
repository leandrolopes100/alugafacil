from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import F, Q, Sum

from apps.contracts.models import (
    FORMA_ABATIDO_CAUCAO, AdicionalContrato, AvariaContrato, Contrato, PagamentoContrato, ParcelaContrato,
)
from apps.financeiro.models import ContaReceber, DespesaOperacional, MultaTransito


class Command(BaseCommand):
    help = 'Lista registros gravados pelos fluxos antigos que deixaram contratos e financeiro incoerentes. Não altera nada.'

    def handle(self, *args, **opcoes):
        verificacoes = [
            (
                'Caução recebido mas contrato ainda com caução "Pendente" (não seria devolvido nem abatido)',
                self._caucao_recebido_pendente,
            ),
            (
                'Caução marcado como pago mas parcela de caução ainda em aberto na agenda/régua',
                self._parcela_caucao_aberta_com_caucao_pago,
            ),
            (
                'Pagamento avulso de "Multa" em contrato com cobrança de multa ainda aberta (possível cobrança em dobro)',
                self._multa_avulsa_com_parcela_aberta,
            ),
            (
                'Pagamento de "Avaria" sem avaria ligada, com avaria ainda "Cobrada" no contrato',
                self._avaria_avulsa_com_avaria_cobrada,
            ),
            (
                'Parcela quitada por valor menor que o nominal (desconto ou pagamento parcial perdoado — conferir)',
                self._parcela_quitada_a_menor,
            ),
            (
                'Cobrança de multa cancelada no encerramento antecipado com a multa ainda "Cobrada ao cliente"',
                self._multa_cancelada_na_quebra,
            ),
            (
                'Contrato cancelado depois da saída do veículo (dívida pode ter sido apagada)',
                self._contrato_ativo_cancelado,
            ),
            (
                'Conta a Receber cancelada com contrato não cancelado (conta congelada)',
                self._conta_cancelada_manual,
            ),
            (
                'Semana cobrada de período que começou depois da devolução do veículo (cobrança indevida)',
                self._semana_cobrada_apos_devolucao,
            ),
            (
                'Parcela de débito automático paga depois do vencimento (corrigir com corrigir_data_debito_automatico)',
                self._debito_automatico_fora_do_vencimento,
            ),
            (
                'Valor negativo ou zerado em avaria, adicional, multa, despesa ou contrato',
                self._valores_invalidos,
            ),
        ]
        total = 0
        for titulo, verificacao in verificacoes:
            linhas = list(verificacao())
            total += len(linhas)
            estilo = self.style.WARNING if linhas else self.style.SUCCESS
            self.stdout.write(estilo(f'\n[{len(linhas)}] {titulo}'))
            for linha in linhas:
                self.stdout.write(f'    {linha}')
        self.stdout.write('')
        if total:
            self.stdout.write(self.style.WARNING(f'{total} registro(s) para conferir. Nada foi alterado.'))
        else:
            self.stdout.write(self.style.SUCCESS('Nenhuma incoerência encontrada.'))

    def _caucao_recebido_pendente(self):
        contratos = (
            Contrato.objects.filter(caucao_situacao='pendente', caucao_valor__gt=0)
            .annotate(coletado=Sum('pagamentos__valor', filter=Q(pagamentos__tipo='caucao')))
            .filter(coletado__gte=F('caucao_valor'))
        )
        for c in contratos:
            yield f'{c.numero} ({c.get_situacao_display()}): caução {c.caucao_valor}, recebido {c.coletado}'

    def _parcela_caucao_aberta_com_caucao_pago(self):
        parcelas = ParcelaContrato.objects.filter(
            tipo='caucao', situacao__in=['pendente', 'em_atraso'],
            contrato__caucao_situacao__in=['pago', 'retido', 'devolvido', 'devolvido_parcial'],
        ).select_related('contrato')
        for p in parcelas:
            yield f'{p.contrato.numero}: parcela {p.numero} de {p.valor}, caução {p.contrato.get_caucao_situacao_display()}'

    def _multa_avulsa_com_parcela_aberta(self):
        pagamentos = PagamentoContrato.objects.filter(
            tipo='multa', parcela__isnull=True,
            contrato__parcelas__tipo='multa', contrato__parcelas__situacao__in=['pendente', 'em_atraso'],
        ).distinct().select_related('contrato')
        for p in pagamentos:
            yield f'{p.contrato.numero}: pagamento de {p.valor} em {p.data_pagamento:%d/%m/%Y}'

    def _avaria_avulsa_com_avaria_cobrada(self):
        pagamentos = PagamentoContrato.objects.filter(
            tipo='avaria', avaria__isnull=True,
            contrato__avarias__situacao='cobrada',
        ).exclude(forma_pagamento=FORMA_ABATIDO_CAUCAO).distinct().select_related('contrato')
        for p in pagamentos:
            yield f'{p.contrato.numero}: pagamento de {p.valor} em {p.data_pagamento:%d/%m/%Y}'

    def _parcela_quitada_a_menor(self):
        pagamentos = PagamentoContrato.objects.filter(
            parcela__isnull=False, parcela__situacao='pago', valor__lt=F('parcela__valor'),
        ).exclude(forma_pagamento=FORMA_ABATIDO_CAUCAO).select_related('contrato', 'parcela', 'registrado_por')
        for p in pagamentos:
            yield (
                f'{p.contrato.numero}: {p.parcela.rotulo} de {p.parcela.valor} quitada com {p.valor} '
                f'em {p.data_pagamento:%d/%m/%Y} por {p.registrado_por or "—"} — obs: {(p.observacoes or "—")[:60]}'
            )

    def _multa_cancelada_na_quebra(self):
        multas = MultaTransito.objects.filter(
            situacao='cobrada_cliente', parcela__situacao='cancelada',
            contrato__encerramento_antecipado=True,
        ).select_related('contrato', 'parcela')
        for m in multas:
            yield f'{m.contrato.numero}: multa de {m.valor} ({m.data_infracao:%d/%m/%Y})'

    def _contrato_ativo_cancelado(self):
        contratos = Contrato.objects.filter(situacao='cancelado', data_saida__isnull=False)
        for c in contratos:
            canceladas = c.parcelas.filter(situacao='cancelada').exclude(tipo='caucao').aggregate(s=Sum('valor'))['s']
            yield f'{c.numero}: saída em {c.data_saida:%d/%m/%Y}, parcelas canceladas somando {canceladas or 0}'

    def _conta_cancelada_manual(self):
        contas = ContaReceber.objects.filter(situacao='cancelado').exclude(
            contrato__situacao='cancelado',
        ).select_related('contrato')
        for c in contas:
            yield f'{c.contrato.numero} ({c.contrato.get_situacao_display()}): total {c.valor_total}, pago {c.valor_pago}'

    def _semana_cobrada_apos_devolucao(self):
        contratos = Contrato.objects.filter(
            data_saida__isnull=False, data_devolucao_real__isnull=False,
            situacao__in=['aguardando_devolucao', 'encerrado'],
        )
        for contrato in contratos:
            limite = contrato.inicio_periodo_nao_usado(contrato.data_devolucao_real)
            parcelas = contrato.parcelas.filter(tipo__in=['semanal', 'mensal']).exclude(situacao='cancelada').filter(
                Q(inicio_periodo__gte=limite) | Q(inicio_periodo__isnull=True, data_vencimento__gte=limite)
            )
            for p in parcelas:
                yield (
                    f'{contrato.numero}: parcela {p.numero} ({p.get_situacao_display()}, {p.valor}) '
                    f'cobre a partir de {(p.inicio_periodo or p.data_vencimento):%d/%m/%Y}; '
                    f'veículo devolvido em {timezone.localtime(contrato.data_devolucao_real):%d/%m/%Y %H:%M}'
                )

    def _debito_automatico_fora_do_vencimento(self):
        for p in DespesaOperacional.parcelas_auto_pagas_fora_do_vencimento():
            yield (
                f'{p.despesa.descricao[:40]} — parcela {p.numero}: vencimento {p.data_vencimento:%d/%m/%Y}, '
                f'pago em {p.data_pagamento:%d/%m/%Y}'
            )

    def _valores_invalidos(self):
        for a in AvariaContrato.objects.filter(valor_cobrado__lt=0).select_related('contrato'):
            yield f'{a.contrato.numero}: avaria "{a.descricao[:40]}" com valor {a.valor_cobrado}'
        for a in AdicionalContrato.objects.filter(Q(diaria__lte=0) | Q(quantidade__lt=1)).select_related('contrato'):
            yield f'{a.contrato.numero}: adicional {a.get_tipo_display()} com diária {a.diaria} e quantidade {a.quantidade}'
        for m in MultaTransito.objects.filter(valor__lte=0).select_related('veiculo'):
            yield f'Multa {m.veiculo.placa} de {m.data_infracao:%d/%m/%Y} com valor {m.valor}'
        for d in DespesaOperacional.objects.filter(valor__lte=0):
            yield f'Despesa "{d.descricao[:40]}" de {d.data_competencia:%d/%m/%Y} com valor {d.valor}'
        for c in Contrato.objects.filter(Q(diaria__lte=0) | Q(valor_km_excedente__lt=0) | Q(caucao_valor__lt=0)):
            yield f'{c.numero}: diária {c.diaria}, km excedente {c.valor_km_excedente}, caução {c.caucao_valor}'
