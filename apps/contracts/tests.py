from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from django_tenants.test.cases import TenantTestCase

from apps.customers.models import CNHCliente, Cliente
from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo
from apps.financeiro.models import ConfiguracaoLocadora

from .models import Contrato, ParcelaContrato, PagamentoContrato, Reserva, gerar_parcelas
from .views import ContratoCheckoutView


class ContratosTestBase(TenantTestCase):
    """Base com a frota/cliente mínimos para montar contratos e reservas."""

    def setUp(self):
        categoria = CategoriaVeiculo.objects.create(nome='Economico')
        self.grupo = GrupoVeiculo.objects.create(
            nome='Grupo A', categoria=categoria,
            diaria=Decimal('100.00'), km_franquia_diaria=200,
            valor_km_excedente=Decimal('1.50'), caucao=Decimal('500.00'),
        )
        self.veiculo = Veiculo.objects.create(
            placa='ABC1D23', marca='Chevrolet', modelo='Onix',
            ano_fabricacao=2023, ano_modelo=2024, cor='Prata',
            grupo=self.grupo,
        )
        self.cliente = Cliente.objects.create(nome='Cliente Teste')

    def criar_contrato(self, **kwargs):
        agora = timezone.now()
        dados = dict(
            cliente=self.cliente,
            veiculo=self.veiculo,
            diaria=self.grupo.diaria,
            km_franquia_diaria=self.grupo.km_franquia_diaria,
            valor_km_excedente=self.grupo.valor_km_excedente,
            data_devolucao_prevista=agora + timedelta(days=7),
        )
        dados.update(kwargs)
        return Contrato.objects.create(**dados)


class GerarParcelasTests(ContratosTestBase):
    """Cobre o modelo de cobrança descrito em gerar_parcelas: 1ª semana upfront,
    demais a cada 7 dias, e proporcional para contratos < 7 dias."""

    def test_contrato_curto_gera_uma_parcela_proporcional_no_vencimento(self):
        contrato = self.criar_contrato()
        inicio = timezone.now().date()
        fim = inicio + timedelta(days=3)

        qtd = gerar_parcelas(contrato, inicio, fim)

        self.assertEqual(qtd, 1)
        parcela = contrato.parcelas.get()
        self.assertEqual(parcela.data_vencimento, fim)
        self.assertEqual(parcela.valor, contrato.diaria * 3)

    def test_contrato_de_sete_dias_gera_uma_parcela_no_inicio(self):
        contrato = self.criar_contrato()
        inicio = timezone.now().date()
        fim = inicio + timedelta(days=7)

        qtd = gerar_parcelas(contrato, inicio, fim)

        self.assertEqual(qtd, 1)
        parcela = contrato.parcelas.get()
        self.assertEqual(parcela.data_vencimento, inicio)
        self.assertEqual(parcela.valor, contrato.diaria * 7)

    def test_contrato_de_trinta_dias_gera_cinco_parcelas_semanais(self):
        contrato = self.criar_contrato()
        inicio = timezone.now().date()
        fim = inicio + timedelta(days=30)

        qtd = gerar_parcelas(contrato, inicio, fim)

        self.assertEqual(qtd, 5)
        vencimentos = list(
            contrato.parcelas.order_by('numero').values_list('data_vencimento', flat=True)
        )
        self.assertEqual(vencimentos, [inicio + timedelta(days=7 * i) for i in range(5)])

    def test_numeracao_continua_em_prorrogacao(self):
        contrato = self.criar_contrato()
        inicio = timezone.now().date()
        meio = inicio + timedelta(days=7)
        fim = meio + timedelta(days=7)

        gerar_parcelas(contrato, inicio, meio, origem='original')
        gerar_parcelas(contrato, meio, fim, origem='prorrogacao')

        parcelas = list(contrato.parcelas.order_by('numero'))
        self.assertEqual([p.numero for p in parcelas], [1, 2])
        self.assertEqual([p.origem for p in parcelas], ['original', 'prorrogacao'])

    def test_periodo_invalido_nao_gera_parcelas(self):
        contrato = self.criar_contrato()
        hoje = timezone.now().date()

        self.assertEqual(gerar_parcelas(contrato, hoje, hoje), 0)
        self.assertEqual(gerar_parcelas(contrato, hoje, hoje - timedelta(days=1)), 0)
        self.assertEqual(contrato.parcelas.count(), 0)


class CalcularFechamentoTests(ContratosTestBase):
    """Cobre Contrato.calcular_fechamento(): km excedente, dias extras e
    diferença de combustível — o coração do encerramento de um contrato."""

    def test_calcula_dias_extras_e_km_excedente(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            data_saida=agora - timedelta(days=10),
            data_devolucao_prevista=agora - timedelta(days=3),
            data_devolucao_real=agora,
            km_saida=10000,
            km_devolucao=12500,
            combustivel_saida='cheio',
            combustivel_devolucao='cheio',
        )

        contrato.calcular_fechamento()

        self.assertEqual(contrato.km_total, 2500)
        self.assertEqual(contrato.total_dias, 10)
        self.assertEqual(contrato.dias_extras, 3)
        self.assertEqual(contrato.valor_dias_extras, contrato.diaria * 3)
        # franquia: 200 km/dia * 10 dias = 2000 -> excedente de 500 km
        self.assertEqual(contrato.km_excedente, 500)
        self.assertEqual(
            contrato.valor_km_excedente_total,
            contrato.valor_km_excedente * 500,
        )
        self.assertEqual(contrato.valor_diferenca_combustivel, Decimal('0.00'))

    def test_cobra_diferenca_de_combustivel_por_quarto_de_tanque_faltante(self):
        ConfiguracaoLocadora.objects.create(pk=1, custo_reposicao_combustivel=Decimal('20.00'))
        agora = timezone.now()
        contrato = self.criar_contrato(
            data_saida=agora - timedelta(days=2),
            data_devolucao_prevista=agora,
            data_devolucao_real=agora,
            km_saida=1000, km_devolucao=1100,
            combustivel_saida='cheio', combustivel_devolucao='1/2',
        )

        contrato.calcular_fechamento()

        # cheio (100%) -> 1/2 (50%): faltam 2 quartos de tanque
        self.assertEqual(contrato.valor_diferenca_combustivel, Decimal('40.00'))

    def test_nao_cobra_diferenca_quando_devolve_com_mais_combustivel(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            data_saida=agora - timedelta(days=1),
            data_devolucao_prevista=agora,
            data_devolucao_real=agora,
            km_saida=1000, km_devolucao=1050,
            combustivel_saida='1/2', combustivel_devolucao='cheio',
        )

        contrato.calcular_fechamento()

        self.assertEqual(contrato.valor_diferenca_combustivel, Decimal('0.00'))

    def test_nao_calcula_sem_dados_completos_de_checkin_e_checkout(self):
        contrato = self.criar_contrato()

        contrato.calcular_fechamento()

        self.assertIsNone(contrato.km_total)
        self.assertIsNone(contrato.total_dias)


class ContratoTotaisTests(ContratosTestBase):
    """Cobre os cálculos financeiros do contrato (total geral, pago, saldo)."""

    def test_totais_excluem_caucao_do_saldo_de_locacao(self):
        contrato = self.criar_contrato(diaria=Decimal('100.00'), total_dias=5)
        contrato.adicionais.create(tipo='gps', diaria=Decimal('10.00'), quantidade=1)
        contrato.avarias.create(
            descricao='Risco na lateral', valor_cobrado=Decimal('150.00'), situacao='cobrada'
        )
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('300.00')
        )
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='dinheiro', tipo='caucao', valor=Decimal('500.00')
        )

        self.assertEqual(contrato.total_locacao, Decimal('500.00'))     # diaria * 5 dias
        self.assertEqual(contrato.total_adicionais, Decimal('50.00'))   # 10 * 1 * 5 dias
        self.assertEqual(contrato.total_avarias, Decimal('150.00'))
        self.assertEqual(contrato.total_geral, Decimal('700.00'))
        self.assertEqual(contrato.total_pago, Decimal('300.00'))        # exclui caução
        self.assertEqual(contrato.total_caucao_coletado, Decimal('500.00'))
        self.assertEqual(contrato.saldo_devedor, Decimal('400.00'))

    def test_avaria_isenta_nao_entra_no_total(self):
        contrato = self.criar_contrato(total_dias=1)
        contrato.avarias.create(descricao='Arranhão leve', valor_cobrado=Decimal('80.00'), situacao='isenta')
        contrato.avarias.create(descricao='Para-choque', valor_cobrado=Decimal('300.00'), situacao='cobrada')

        self.assertEqual(contrato.total_avarias, Decimal('300.00'))


class ContratoNumeracaoTests(ContratosTestBase):
    """Cobre a geração do número sequencial AF-<ano>-<seq> em Contrato.save()."""

    def test_gera_numero_sequencial_no_formato_esperado(self):
        c1 = self.criar_contrato()
        c2 = self.criar_contrato()
        ano = timezone.now().year

        self.assertEqual(c1.numero, f'AF-{ano}-0001')
        self.assertEqual(c2.numero, f'AF-{ano}-0002')

    def test_nao_gera_novo_numero_ao_salvar_novamente(self):
        contrato = self.criar_contrato()
        numero_original = contrato.numero

        contrato.obs_saida = 'Tanque cheio na saída'
        contrato.save()

        self.assertEqual(contrato.numero, numero_original)


class ReservaTests(ContratosTestBase):
    """Cobre as propriedades de cotação de Reserva usadas na conversão para contrato."""

    def test_dias_previstos_arredonda_periodo_parcial_para_cima(self):
        agora = timezone.now()
        reserva = Reserva.objects.create(
            cliente=self.cliente, grupo_veiculo=self.grupo,
            data_retirada=agora, data_devolucao=agora + timedelta(hours=25),
            diaria_cotada=Decimal('120.00'),
        )

        self.assertEqual(reserva.dias_previstos, 2)
        self.assertEqual(reserva.total_cotado, Decimal('240.00'))

    def test_total_cotado_e_zero_sem_diaria_cotada(self):
        agora = timezone.now()
        reserva = Reserva.objects.create(
            cliente=self.cliente, grupo_veiculo=self.grupo,
            data_retirada=agora, data_devolucao=agora + timedelta(days=1),
        )

        self.assertEqual(reserva.total_cotado, Decimal('0.00'))


class ParcelaContratoCorrecaoTests(ContratosTestBase):
    """Cobre ParcelaContrato.valor_corrigido (multa + juros sobre atraso)."""

    def test_aplica_multa_e_juros_apos_a_carencia(self):
        ConfiguracaoLocadora.objects.create(
            pk=1,
            percentual_multa_atraso=Decimal('2.00'),
            percentual_juros_diario=Decimal('0.10'),
            dias_carencia=1,
        )
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date() - timedelta(days=4),
            valor=Decimal('700.00'), situacao='em_atraso',
        )

        # dias_atraso = 4; dias_efetivos = 4 - 1 (carência) = 3
        multa = Decimal('700.00') * (Decimal('2.00') / Decimal('100'))
        juros = Decimal('700.00') * (Decimal('0.10') / Decimal('100')) * 3
        esperado = (Decimal('700.00') + multa + juros).quantize(Decimal('0.01'))

        self.assertEqual(parcela.valor_corrigido, esperado)

    def test_nao_corrige_valor_dentro_do_periodo_de_carencia(self):
        ConfiguracaoLocadora.objects.create(pk=1, dias_carencia=5)
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date() - timedelta(days=2),
            valor=Decimal('500.00'), situacao='em_atraso',
        )

        self.assertEqual(parcela.valor_corrigido, Decimal('500.00'))

    def test_em_atraso_e_dias_atraso_para_pendente_vencida(self):
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date() - timedelta(days=1),
            valor=Decimal('100.00'), situacao='pendente',
        )

        self.assertTrue(parcela.em_atraso)
        self.assertEqual(parcela.dias_atraso, 1)


class ChecklistCheckoutTests(ContratosTestBase):
    """Cobre ContratoCheckoutView._checklist — a regra que decide se o veículo
    pode ou não ser liberado ao cliente (núcleo do fluxo operacional)."""

    def setUp(self):
        super().setUp()
        self.view = ContratoCheckoutView()

    def _falhas(self, contrato):
        return [msg for ok, msg in self.view._checklist(contrato) if not ok]

    def test_bloqueia_cliente_bloqueado_e_sem_cnh_cadastrada(self):
        self.cliente.situacao = 'bloqueado'
        self.cliente.motivo_bloqueio = 'Inadimplente em contrato anterior'
        self.cliente.save()
        contrato = self.criar_contrato(caucao_valor=Decimal('0.00'))

        falhas = self._falhas(contrato)

        self.assertTrue(any('Cliente bloqueado' in m for m in falhas))
        self.assertTrue(any('sem CNH cadastrada' in m for m in falhas))

    def test_bloqueia_cnh_vencida_e_caucao_pendente(self):
        CNHCliente.objects.create(
            cliente=self.cliente, numero='12345678900', estado_emissor='SP',
            categoria='b', validade=timezone.now().date() - timedelta(days=10),
        )
        contrato = self.criar_contrato(caucao_valor=Decimal('500.00'), caucao_situacao='pendente')

        falhas = self._falhas(contrato)

        self.assertTrue(any('CNH vencida' in m for m in falhas))
        self.assertTrue(any('Caução' in m and 'não registrado' in m for m in falhas))

    def test_bloqueia_quando_primeira_semana_nao_foi_paga(self):
        CNHCliente.objects.create(
            cliente=self.cliente, numero='12345678900', estado_emissor='SP',
            categoria='b', validade=timezone.now().date() + timedelta(days=365),
        )
        contrato = self.criar_contrato(caucao_valor=Decimal('0.00'))

        falhas = self._falhas(contrato)

        self.assertTrue(any('Primeira semana' in m for m in falhas))

    def test_aprova_checklist_quando_tudo_esta_em_ordem(self):
        CNHCliente.objects.create(
            cliente=self.cliente, numero='12345678900', estado_emissor='SP',
            categoria='b', validade=timezone.now().date() + timedelta(days=365),
        )
        contrato = self.criar_contrato(caucao_valor=Decimal('0.00'))
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='locacao',
            valor=contrato.diaria * 7,
        )

        self.assertEqual(self._falhas(contrato), [])
