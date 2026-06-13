from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from django_tenants.test.cases import TenantTestCase

from apps.contracts.models import Contrato, PagamentoContrato
from apps.customers.models import Cliente
from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo

from .models import (
    ConfiguracaoLocadora, ContaReceber, DespesaOperacional, MultaTransito,
    ParcelaDespesa, gerar_parcelas_despesa,
)


class FinanceiroTestBase(TenantTestCase):
    """Base com a frota/cliente mínimos para montar contratos, contas e despesas."""

    def setUp(self):
        categoria = CategoriaVeiculo.objects.create(nome='Economico')
        self.grupo = GrupoVeiculo.objects.create(
            nome='Grupo A', categoria=categoria, diaria=Decimal('100.00'),
        )
        self.veiculo = Veiculo.objects.create(
            placa='ABC1D23', marca='Fiat', modelo='Argo',
            ano_fabricacao=2023, ano_modelo=2024, cor='Branco', grupo=self.grupo,
        )
        self.cliente = Cliente.objects.create(nome='Cliente Teste')

    def criar_contrato(self, **kwargs):
        agora = timezone.now()
        dados = dict(
            cliente=self.cliente, veiculo=self.veiculo, diaria=Decimal('100.00'),
            data_devolucao_prevista=agora + timedelta(days=7),
        )
        dados.update(kwargs)
        return Contrato.objects.create(**dados)

    def criar_conta_receber(self, contrato, **kwargs):
        dados = dict(
            contrato=contrato, cliente=self.cliente, descricao='Locação',
            valor_total=Decimal('700.00'),
            data_emissao=date.today(),
            data_vencimento=date.today() + timedelta(days=10),
        )
        dados.update(kwargs)
        return ContaReceber.objects.create(**dados)


class ContaReceberAtualizarSituacaoTests(FinanceiroTestBase):
    """Cobre ContaReceber.atualizar_situacao — a reconciliação entre os
    pagamentos do contrato e a situação da conta a receber."""

    def test_marca_como_pago_quando_total_e_quitado(self):
        contrato = self.criar_contrato()
        conta = self.criar_conta_receber(contrato, valor_total=Decimal('700.00'))
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('700.00')
        )

        conta.atualizar_situacao()

        self.assertEqual(conta.situacao, 'pago')
        self.assertEqual(conta.valor_pago, Decimal('700.00'))
        self.assertEqual(conta.valor_saldo, Decimal('0.00'))

    def test_marca_como_pago_parcial_quando_falta_saldo(self):
        contrato = self.criar_contrato()
        conta = self.criar_conta_receber(contrato, valor_total=Decimal('700.00'))
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('300.00')
        )

        conta.atualizar_situacao()

        self.assertEqual(conta.situacao, 'pago_parcial')
        self.assertEqual(conta.valor_saldo, Decimal('400.00'))

    def test_marca_como_vencido_quando_data_passou_sem_pagamento(self):
        contrato = self.criar_contrato()
        conta = self.criar_conta_receber(
            contrato, valor_total=Decimal('700.00'),
            data_emissao=date.today() - timedelta(days=20),
            data_vencimento=date.today() - timedelta(days=5),
        )

        conta.atualizar_situacao()

        self.assertEqual(conta.situacao, 'vencido')
        self.assertEqual(conta.dias_em_atraso, 5)

    def test_caucao_nao_conta_como_pagamento_de_locacao(self):
        contrato = self.criar_contrato()
        conta = self.criar_conta_receber(contrato, valor_total=Decimal('700.00'))
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='caucao', valor=Decimal('500.00')
        )

        conta.atualizar_situacao()

        self.assertEqual(conta.situacao, 'pendente')
        self.assertEqual(conta.valor_pago, Decimal('0.00'))

    def test_conta_cancelada_nao_e_recalculada(self):
        contrato = self.criar_contrato()
        conta = self.criar_conta_receber(
            contrato, valor_total=Decimal('700.00'), situacao='cancelado'
        )
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('700.00')
        )

        conta.atualizar_situacao()
        conta.refresh_from_db()

        self.assertEqual(conta.situacao, 'cancelado')
        self.assertEqual(conta.valor_pago, Decimal('0.00'))

    def test_atualiza_valor_total_e_vencimento_em_prorrogacao(self):
        contrato = self.criar_contrato()
        conta = self.criar_conta_receber(contrato, valor_total=Decimal('700.00'))
        novo_vencimento = date.today() + timedelta(days=20)

        conta.atualizar_situacao(novo_valor_total=Decimal('1000.00'), novo_vencimento=novo_vencimento)

        self.assertEqual(conta.valor_total, Decimal('1000.00'))
        self.assertEqual(conta.data_vencimento, novo_vencimento)
        self.assertEqual(conta.situacao, 'pendente')


class GerarParcelasDespesaTests(FinanceiroTestBase):
    """Cobre gerar_parcelas_despesa: rateio mensal com resíduo na última parcela
    e respeito ao fim de mês ao somar meses."""

    def test_distribui_total_em_parcelas_iguais_com_residuo_na_ultima(self):
        despesa = DespesaOperacional.objects.create(
            categoria='seguro', descricao='Seguro frota', valor=Decimal('1000.00'),
            data_competencia=date(2026, 1, 15), parcelado=True, numero_parcelas=3,
        )

        gerar_parcelas_despesa(despesa)

        valores = list(despesa.parcelas.order_by('numero').values_list('valor', flat=True))
        self.assertEqual(valores, [Decimal('333.33'), Decimal('333.33'), Decimal('333.34')])
        self.assertEqual(sum(valores), despesa.valor)

    def test_vencimentos_mensais_respeitam_fim_de_mes(self):
        despesa = DespesaOperacional.objects.create(
            categoria='seguro', descricao='Seguro frota', valor=Decimal('300.00'),
            data_competencia=date(2026, 1, 31), parcelado=True, numero_parcelas=2,
        )

        gerar_parcelas_despesa(despesa)

        vencimentos = list(despesa.parcelas.order_by('numero').values_list('data_vencimento', flat=True))
        # Fevereiro/2026 não é bissexto: dia 31 -> cai no último dia (28)
        self.assertEqual(vencimentos, [date(2026, 2, 28), date(2026, 3, 31)])


class DespesaOperacionalTests(FinanceiroTestBase):
    """Cobre DespesaOperacional.pago / progresso_parcelas e a confirmação
    automática de débito automático."""

    def test_despesa_simples_fica_paga_apenas_com_data_pagamento(self):
        despesa = DespesaOperacional.objects.create(
            categoria='lavagem', descricao='Lavagem geral', valor=Decimal('80.00'),
            data_competencia=date.today(),
        )

        self.assertFalse(despesa.pago)

        despesa.data_pagamento = date.today()
        despesa.save()

        self.assertTrue(despesa.pago)

    def test_despesa_parcelada_so_fica_paga_com_todas_as_parcelas_pagas(self):
        despesa = DespesaOperacional.objects.create(
            categoria='seguro', descricao='Seguro frota', valor=Decimal('600.00'),
            data_competencia=date.today(), parcelado=True, numero_parcelas=2,
        )
        gerar_parcelas_despesa(despesa)
        self.assertEqual(despesa.progresso_parcelas, (0, 2))
        self.assertFalse(despesa.pago)

        primeira = despesa.parcelas.get(numero=1)
        primeira.situacao = 'pago'
        primeira.save()
        self.assertEqual(despesa.progresso_parcelas, (1, 2))
        self.assertFalse(despesa.pago)

        segunda = despesa.parcelas.get(numero=2)
        segunda.situacao = 'pago'
        segunda.save()
        self.assertEqual(despesa.progresso_parcelas, (2, 2))
        self.assertTrue(despesa.pago)

    def test_sincronizar_auto_pagamento_confirma_parcelas_de_debito_automatico_vencidas(self):
        despesa = DespesaOperacional.objects.create(
            categoria='aluguel', descricao='Aluguel escritório', valor=Decimal('1200.00'),
            data_competencia=date.today(), debito_automatico=True,
        )
        parcela = ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('1200.00'),
            data_vencimento=date.today() - timedelta(days=1), situacao='pendente',
        )
        outra_despesa = DespesaOperacional.objects.create(
            categoria='salario', descricao='Salário atendente', valor=Decimal('2000.00'),
            data_competencia=date.today(), debito_automatico=False,
        )
        parcela_manual = ParcelaDespesa.objects.create(
            despesa=outra_despesa, numero=1, valor=Decimal('2000.00'),
            data_vencimento=date.today() - timedelta(days=1), situacao='pendente',
        )

        confirmadas = DespesaOperacional.sincronizar_auto_pagamento()

        parcela.refresh_from_db()
        parcela_manual.refresh_from_db()
        self.assertEqual(confirmadas, 1)
        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(parcela.data_pagamento, date.today())
        self.assertEqual(parcela_manual.situacao, 'pendente')


class ParcelaDespesaTests(FinanceiroTestBase):
    """Cobre ParcelaDespesa.em_atraso / dias_atraso (usados na agenda de pagamentos)."""

    def test_em_atraso_e_dias_atraso_para_parcela_pendente_vencida(self):
        despesa = DespesaOperacional.objects.create(
            categoria='aluguel', descricao='Aluguel escritório', valor=Decimal('1200.00'),
            data_competencia=date.today(),
        )
        parcela = ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('1200.00'),
            data_vencimento=date.today() - timedelta(days=3), situacao='pendente',
        )

        self.assertTrue(parcela.em_atraso)
        self.assertEqual(parcela.dias_atraso, 3)

    def test_parcela_paga_nao_esta_em_atraso(self):
        despesa = DespesaOperacional.objects.create(
            categoria='aluguel', descricao='Aluguel escritório', valor=Decimal('1200.00'),
            data_competencia=date.today(),
        )
        parcela = ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('1200.00'),
            data_vencimento=date.today() - timedelta(days=3),
            situacao='pago', data_pagamento=date.today(),
        )

        self.assertFalse(parcela.em_atraso)
        self.assertEqual(parcela.dias_atraso, 0)


class MultaTransitoTests(FinanceiroTestBase):
    """Cobre o vínculo automático multa <-> contrato vigente na data da infração."""

    def test_vincula_automaticamente_ao_contrato_vigente_na_data_da_infracao(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            situacao='ativo',
            data_saida=agora - timedelta(days=5),
            data_devolucao_prevista=agora + timedelta(days=2),
        )

        multa = MultaTransito.objects.create(
            veiculo=self.veiculo,
            data_infracao=(agora - timedelta(days=2)).date(),
            descricao='Excesso de velocidade', valor=Decimal('195.23'),
        )

        self.assertEqual(multa.contrato_id, contrato.id)

    def test_nao_vincula_quando_nao_ha_contrato_vigente_na_data(self):
        multa = MultaTransito.objects.create(
            veiculo=self.veiculo,
            data_infracao=date.today() - timedelta(days=100),
            descricao='Estacionamento irregular', valor=Decimal('88.38'),
        )

        self.assertIsNone(multa.contrato)

    def test_prazo_critico_e_vencido(self):
        hoje = timezone.now().date()
        critica = MultaTransito.objects.create(
            veiculo=self.veiculo, data_infracao=hoje - timedelta(days=10),
            descricao='Avanço de sinal', valor=Decimal('293.47'),
            prazo_indicacao=hoje + timedelta(days=3),
        )
        vencida = MultaTransito.objects.create(
            veiculo=self.veiculo, data_infracao=hoje - timedelta(days=40),
            descricao='Velocidade', valor=Decimal('130.16'),
            prazo_indicacao=hoje - timedelta(days=2),
        )

        self.assertTrue(critica.prazo_critico)
        self.assertFalse(critica.prazo_vencido)
        self.assertTrue(vencida.prazo_vencido)


class ConfiguracaoLocadoraTests(FinanceiroTestBase):
    """Cobre o singleton ConfiguracaoLocadora.obter()."""

    def test_obter_cria_configuracao_padrao_uma_unica_vez(self):
        self.assertEqual(ConfiguracaoLocadora.objects.count(), 0)

        primeira = ConfiguracaoLocadora.obter()
        self.assertEqual(ConfiguracaoLocadora.objects.count(), 1)

        primeira.percentual_multa_atraso = Decimal('5.00')
        primeira.save()

        segunda = ConfiguracaoLocadora.obter()
        self.assertEqual(ConfiguracaoLocadora.objects.count(), 1)
        self.assertEqual(segunda.percentual_multa_atraso, Decimal('5.00'))
