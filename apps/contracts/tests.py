from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.customers.models import CNHCliente, Cliente
from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo
from apps.financeiro.models import ConfiguracaoLocadora, DespesaOperacional

from .models import Contrato, ParcelaContrato, PagamentoContrato, Reserva, gerar_parcelas
from .views import ContratoCheckoutView


class ContratosTestBase(TestCase):
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

    def test_ultima_parcela_semanal_e_proporcional_ao_ciclo_parcial(self):
        """Fase 8 do plano de correcao do laudo: 10 dias = 1 semana cheia +
        3 dias -- a ultima parcela deve cobrar so os 3 dias, nao a semana
        inteira (achado 7 do laudo tecnico financeiro)."""
        contrato = self.criar_contrato()
        inicio = timezone.now().date()
        fim = inicio + timedelta(days=10)

        qtd = gerar_parcelas(contrato, inicio, fim)

        self.assertEqual(qtd, 2)
        parcelas = list(contrato.parcelas.order_by('numero'))
        self.assertEqual(parcelas[0].valor, contrato.diaria * 7)
        self.assertEqual(parcelas[1].valor, contrato.diaria * 3)

    def test_periodo_multiplo_exato_de_semana_continua_cobrando_semana_cheia(self):
        contrato = self.criar_contrato()
        inicio = timezone.now().date()
        fim = inicio + timedelta(days=14)

        gerar_parcelas(contrato, inicio, fim)

        parcelas = list(contrato.parcelas.order_by('numero'))
        self.assertEqual(len(parcelas), 2)
        for p in parcelas:
            self.assertEqual(p.valor, contrato.diaria * 7)


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


class ContratoEncerrarCaucaoTests(ContratosTestBase):
    """Cobre ContratoEncerrarView: retencao de caucao deve se limitar ao valor
    efetivo das avarias cobradas, devolvendo o excedente automaticamente.
    Fase 1 do plano de correcao do laudo tecnico financeiro."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser('auditor', 'auditor@teste.com', 'senha123')
        self.client.force_login(self.user)

    def _encerrar(self, contrato):
        return self.client.post(reverse('contratos:encerrar', kwargs={'pk': contrato.pk}))

    def test_sem_avaria_cobrada_devolve_caucao_integral_sem_criar_pagamento(self):
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao',
            caucao_valor=Decimal('1000.00'), caucao_situacao='retido',
        )
        # avaria so 'identificada', sem valor definido -- nao deve reter nada
        contrato.avarias.create(descricao='Risco leve', situacao='identificada')

        self._encerrar(contrato)
        contrato.refresh_from_db()

        self.assertEqual(contrato.caucao_situacao, 'devolvido')
        self.assertFalse(PagamentoContrato.objects.filter(contrato=contrato, tipo='avaria').exists())
        despesa = DespesaOperacional.objects.get(observacoes__contains=f'[caucao:{contrato.numero}]')
        self.assertEqual(despesa.valor, Decimal('1000.00'))

    def test_avaria_menor_que_caucao_retem_so_o_valor_da_avaria_e_devolve_resto(self):
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao',
            caucao_valor=Decimal('1000.00'), caucao_situacao='retido',
        )
        contrato.avarias.create(
            descricao='Para-choque arranhado', valor_cobrado=Decimal('200.00'), situacao='cobrada',
        )

        self._encerrar(contrato)
        contrato.refresh_from_db()

        pagamento = PagamentoContrato.objects.get(contrato=contrato, tipo='avaria')
        self.assertEqual(pagamento.valor, Decimal('200.00'))
        self.assertEqual(contrato.caucao_situacao, 'devolvido_parcial')
        despesa = DespesaOperacional.objects.get(observacoes__contains=f'[caucao:{contrato.numero}]')
        self.assertEqual(despesa.valor, Decimal('800.00'))  # excedente devolvido, nao o caucao cheio

    def test_avaria_igual_ao_caucao_retem_tudo_sem_gerar_devolucao(self):
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao',
            caucao_valor=Decimal('1000.00'), caucao_situacao='retido',
        )
        contrato.avarias.create(
            descricao='Colisao lateral', valor_cobrado=Decimal('1000.00'), situacao='cobrada',
        )

        self._encerrar(contrato)
        contrato.refresh_from_db()

        pagamento = PagamentoContrato.objects.get(contrato=contrato, tipo='avaria')
        self.assertEqual(pagamento.valor, Decimal('1000.00'))
        self.assertEqual(contrato.caucao_situacao, 'retido')
        self.assertFalse(DespesaOperacional.objects.filter(observacoes__contains=f'[caucao:{contrato.numero}]').exists())

    def test_avaria_maior_que_caucao_retem_apenas_o_valor_do_caucao(self):
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao',
            caucao_valor=Decimal('1000.00'), caucao_situacao='retido',
        )
        contrato.avarias.create(
            descricao='Perda total do para-lama', valor_cobrado=Decimal('1500.00'), situacao='cobrada',
        )

        self._encerrar(contrato)
        contrato.refresh_from_db()

        pagamento = PagamentoContrato.objects.get(contrato=contrato, tipo='avaria')
        self.assertEqual(pagamento.valor, Decimal('1000.00'))  # nunca mais que o caucao
        self.assertEqual(contrato.caucao_situacao, 'retido')

    def test_avaria_paga_diretamente_nao_conta_para_retencao_do_caucao(self):
        """Avaria ja quitada via ContratoAvariaMarcarPagaView (situacao='paga') nao
        deve ser contada de novo na retencao do caucao -- evita dupla cobranca."""
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao',
            caucao_valor=Decimal('1000.00'), caucao_situacao='retido',
        )
        contrato.avarias.create(
            descricao='Retrovisor quebrado', valor_cobrado=Decimal('300.00'), situacao='paga',
        )

        self._encerrar(contrato)
        contrato.refresh_from_db()

        self.assertEqual(contrato.caucao_situacao, 'devolvido')
        self.assertFalse(PagamentoContrato.objects.filter(contrato=contrato, tipo='avaria').exists())

    def test_duplo_submit_nao_duplica_o_pagamento_de_retencao(self):
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao',
            caucao_valor=Decimal('1000.00'), caucao_situacao='retido',
        )
        contrato.avarias.create(
            descricao='Para-choque arranhado', valor_cobrado=Decimal('200.00'), situacao='cobrada',
        )

        self._encerrar(contrato)
        contrato.refresh_from_db()
        contrato.situacao = 'aguardando_devolucao'
        contrato.caucao_situacao = 'retido'
        contrato.save()

        self._encerrar(contrato)

        self.assertEqual(
            PagamentoContrato.objects.filter(contrato=contrato, tipo='avaria').count(), 1
        )


class PagarParcelaValidacaoTests(ContratosTestBase):
    """Cobre PagarParcelaView: valor divergente de valor_corrigido exige
    observacao justificando a diferenca -- nunca e aceito silenciosamente.
    Fase 3 do plano de correcao do laudo tecnico financeiro."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser('auditor2', 'auditor2@teste.com', 'senha123')
        self.client.force_login(self.user)

    def _pagar(self, contrato, parcela, valor, observacoes=''):
        return self.client.post(
            reverse('contratos:parcela-pagar', kwargs={'pk': contrato.pk, 'parcela_pk': parcela.pk}),
            {'valor': str(valor), 'forma_pagamento': 'pix', 'observacoes': observacoes},
        )

    def test_pagamento_no_valor_correto_sem_atraso_funciona_sem_observacao(self):
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date() + timedelta(days=1),
            valor=Decimal('700.00'), situacao='pendente',
        )

        self._pagar(contrato, parcela, Decimal('700.00'))
        parcela.refresh_from_db()

        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(
            PagamentoContrato.objects.get(contrato=contrato).valor, Decimal('700.00')
        )

    def test_pagamento_com_multa_calculada_corretamente_funciona_sem_observacao(self):
        ConfiguracaoLocadora.objects.create(
            pk=1, percentual_multa_atraso=Decimal('2.00'),
            percentual_juros_diario=Decimal('0.10'), dias_carencia=0,
        )
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date() - timedelta(days=4),
            valor=Decimal('700.00'), situacao='em_atraso',
        )
        valor_corrigido = parcela.valor_corrigido  # inclui multa + juros

        self._pagar(contrato, parcela, valor_corrigido)
        parcela.refresh_from_db()

        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(PagamentoContrato.objects.get(contrato=contrato).valor, valor_corrigido)

    def test_valor_divergente_sem_observacao_e_rejeitado(self):
        ConfiguracaoLocadora.objects.create(
            pk=1, percentual_multa_atraso=Decimal('2.00'),
            percentual_juros_diario=Decimal('0.10'), dias_carencia=0,
        )
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date() - timedelta(days=4),
            valor=Decimal('700.00'), situacao='em_atraso',
        )
        # Operador esquece a multa/juros e tenta pagar so o valor nominal
        self._pagar(contrato, parcela, Decimal('700.00'))
        parcela.refresh_from_db()

        self.assertEqual(parcela.situacao, 'em_atraso')  # nao foi alterado
        self.assertFalse(PagamentoContrato.objects.filter(contrato=contrato).exists())

    def test_valor_divergente_com_observacao_e_aceito(self):
        ConfiguracaoLocadora.objects.create(
            pk=1, percentual_multa_atraso=Decimal('2.00'),
            percentual_juros_diario=Decimal('0.10'), dias_carencia=0,
        )
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date() - timedelta(days=4),
            valor=Decimal('700.00'), situacao='em_atraso',
        )
        self._pagar(
            contrato, parcela, Decimal('700.00'),
            observacoes='Cliente negociou dispensa de multa com a gerência.',
        )
        parcela.refresh_from_db()

        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(PagamentoContrato.objects.get(contrato=contrato).valor, Decimal('700.00'))


class PagamentoContratoVinculoTests(ContratosTestBase):
    """Cobre o vinculo estrutural PagamentoContrato -> ParcelaContrato/AvariaContrato
    (Achado 3 do laudo). Fase 4 do plano de correcao."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser('auditor3', 'auditor3@teste.com', 'senha123')
        self.client.force_login(self.user)

    def test_pagar_parcela_grava_o_vinculo_com_a_parcela(self):
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date() + timedelta(days=1),
            valor=Decimal('700.00'), situacao='pendente',
        )

        self.client.post(
            reverse('contratos:parcela-pagar', kwargs={'pk': contrato.pk, 'parcela_pk': parcela.pk}),
            {'valor': '700.00', 'forma_pagamento': 'pix', 'observacoes': ''},
        )

        pagamento = PagamentoContrato.objects.get(contrato=contrato)
        self.assertEqual(pagamento.parcela_id, parcela.pk)
        self.assertIsNone(pagamento.avaria_id)

    def test_marcar_avaria_paga_grava_o_vinculo_com_a_avaria(self):
        contrato = self.criar_contrato()
        avaria = contrato.avarias.create(
            descricao='Retrovisor quebrado', valor_cobrado=Decimal('150.00'), situacao='cobrada',
        )

        self.client.post(
            reverse('contratos:avaria-pagar', kwargs={'pk': contrato.pk, 'avaria_pk': avaria.pk}),
            {'forma_pagamento': 'dinheiro'},
        )

        pagamento = PagamentoContrato.objects.get(contrato=contrato)
        self.assertEqual(pagamento.avaria_id, avaria.pk)
        self.assertIsNone(pagamento.parcela_id)

    def test_pagamento_historico_sem_vinculo_continua_valido(self):
        """Simula um PagamentoContrato antigo, de antes da Fase 4 -- deve
        continuar existindo e sendo somado normalmente, sem vinculo."""
        contrato = self.criar_contrato()
        pagamento = PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('300.00'),
        )

        self.assertIsNone(pagamento.parcela_id)
        self.assertIsNone(pagamento.avaria_id)
        self.assertEqual(contrato.total_pago, Decimal('300.00'))

    def test_unicidade_impede_dois_pagamentos_vinculados_a_mesma_parcela(self):
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.now().date(), valor=Decimal('700.00'), situacao='pendente',
        )
        PagamentoContrato.objects.create(
            contrato=contrato, parcela=parcela, forma_pagamento='pix',
            tipo='locacao', valor=Decimal('700.00'),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PagamentoContrato.objects.create(
                    contrato=contrato, parcela=parcela, forma_pagamento='dinheiro',
                    tipo='locacao', valor=Decimal('700.00'),
                )

    def test_unicidade_impede_dois_pagamentos_vinculados_a_mesma_avaria(self):
        contrato = self.criar_contrato()
        avaria = contrato.avarias.create(
            descricao='Para-choque', valor_cobrado=Decimal('200.00'), situacao='cobrada',
        )
        PagamentoContrato.objects.create(
            contrato=contrato, avaria=avaria, forma_pagamento='pix',
            tipo='avaria', valor=Decimal('200.00'),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PagamentoContrato.objects.create(
                    contrato=contrato, avaria=avaria, forma_pagamento='dinheiro',
                    tipo='avaria', valor=Decimal('200.00'),
                )


class ReverterCheckinAcertoTests(ContratosTestBase):
    """Cobre ContratoReverterCheckinView: parcela de acerto gerada no check-in
    deve ser cancelada ao reverter (se ainda nao paga), evitando duplicidade
    se o check-in for refeito depois. Fase 5 do plano de correcao do laudo."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser('auditor4', 'auditor4@teste.com', 'senha123')
        self.client.force_login(self.user)

    def _contrato_e_agora_com_excedente(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            situacao='ativo',
            data_saida=agora - timedelta(days=10),
            data_devolucao_prevista=agora - timedelta(days=3),
            km_saida=10000,
            combustivel_saida='cheio',
        )
        return contrato, agora

    def _checkin(self, contrato, data_devolucao_real, km_devolucao=12500):
        # timezone.localtime() antes do strftime -- o form reconstroi a data
        # via make_aware() no fuso local (America/Sao_Paulo); sem essa conversao
        # o valor enviado sofre um deslocamento de fuso ao ser reinterpretado.
        return self.client.post(
            reverse('contratos:checkin', kwargs={'pk': contrato.pk}),
            {
                'data_devolucao_real': timezone.localtime(data_devolucao_real).strftime('%Y-%m-%dT%H:%M'),
                'km_devolucao': str(km_devolucao),
                'combustivel_devolucao': 'cheio',
                'obs_devolucao': '',
            },
        )

    def _reverter(self, contrato):
        return self.client.post(reverse('contratos:reverter-checkin', kwargs={'pk': contrato.pk}))

    def test_checkin_gera_parcela_de_acerto_quando_ha_excedente(self):
        contrato, agora = self._contrato_e_agora_com_excedente()

        self._checkin(contrato, agora)

        acerto = ParcelaContrato.objects.get(contrato=contrato, tipo='acerto')
        self.assertEqual(acerto.situacao, 'pendente')

    def test_reverter_cancela_a_parcela_de_acerto_pendente(self):
        contrato, agora = self._contrato_e_agora_com_excedente()
        self._checkin(contrato, agora)

        self._reverter(contrato)

        acerto = ParcelaContrato.objects.get(contrato=contrato, tipo='acerto')
        self.assertEqual(acerto.situacao, 'cancelada')

    def test_reverter_nao_cancela_acerto_ja_pago(self):
        contrato, agora = self._contrato_e_agora_com_excedente()
        self._checkin(contrato, agora)
        acerto = ParcelaContrato.objects.get(contrato=contrato, tipo='acerto')
        acerto.situacao = 'pago'
        acerto.data_pagamento = timezone.now()
        acerto.save()

        self._reverter(contrato)

        acerto.refresh_from_db()
        self.assertEqual(acerto.situacao, 'pago')

    def test_refazer_checkin_apos_reverter_nao_deixa_duas_parcelas_ativas(self):
        contrato, agora = self._contrato_e_agora_com_excedente()
        self._checkin(contrato, agora)
        self._reverter(contrato)
        contrato.refresh_from_db()

        self._checkin(contrato, agora, km_devolucao=12600)  # excedente ligeiramente diferente

        acertos = ParcelaContrato.objects.filter(contrato=contrato, tipo='acerto')
        self.assertEqual(acertos.count(), 2)  # uma cancelada + uma nova
        ativos = acertos.filter(situacao__in=['pendente', 'em_atraso'])
        self.assertEqual(ativos.count(), 1)

    def test_reverter_sem_acerto_continua_funcionando(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            situacao='ativo',
            data_saida=agora - timedelta(days=5),
            data_devolucao_prevista=agora,
            km_saida=1000,
            combustivel_saida='cheio',
        )
        self._checkin(contrato, agora, km_devolucao=1050)  # sem excedente, sem atraso

        self.assertFalse(ParcelaContrato.objects.filter(contrato=contrato, tipo='acerto').exists())

        self._reverter(contrato)
        contrato.refresh_from_db()
        self.assertEqual(contrato.situacao, 'ativo')


class ContratoProrrogarIdempotenciaTests(ContratosTestBase):
    """Cobre ContratoProrrogarView: um segundo submit identico (duplo clique)
    nao deve duplicar as parcelas do periodo estendido. Fase 6 do plano de
    correcao do laudo tecnico financeiro."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser('auditor5', 'auditor5@teste.com', 'senha123')
        self.client.force_login(self.user)

    def _prorrogar(self, contrato, nova_data):
        return self.client.post(
            reverse('contratos:prorrogar', kwargs={'pk': contrato.pk}),
            {'nova_data_devolucao': timezone.localtime(nova_data).strftime('%Y-%m-%dT%H:%M')},
        )

    def test_prorrogacao_normal_gera_parcelas_para_o_periodo_estendido(self):
        contrato = self.criar_contrato(situacao='ativo')
        data_original = contrato.data_devolucao_prevista
        nova_data = data_original + timedelta(days=14)

        self._prorrogar(contrato, nova_data)
        contrato.refresh_from_db()

        self.assertEqual(contrato.data_devolucao_prevista, nova_data.replace(second=0, microsecond=0))
        self.assertTrue(contrato.parcelas.filter(origem='prorrogacao').exists())

    def test_segundo_submit_identico_nao_duplica_parcelas(self):
        contrato = self.criar_contrato(situacao='ativo')
        nova_data = contrato.data_devolucao_prevista + timedelta(days=14)

        self._prorrogar(contrato, nova_data)
        qtd_parcelas_apos_primeira = ParcelaContrato.objects.filter(contrato=contrato).count()

        # Reenvia exatamente a mesma nova_data (simula duplo clique/reenvio do form)
        self._prorrogar(contrato, nova_data)
        qtd_parcelas_apos_segunda = ParcelaContrato.objects.filter(contrato=contrato).count()

        self.assertEqual(qtd_parcelas_apos_primeira, qtd_parcelas_apos_segunda)

    def test_prorrogacao_subsequente_com_data_maior_ainda_funciona(self):
        contrato = self.criar_contrato(situacao='ativo')
        primeira_extensao = contrato.data_devolucao_prevista + timedelta(days=14)
        self._prorrogar(contrato, primeira_extensao)
        qtd_apos_primeira = ParcelaContrato.objects.filter(contrato=contrato).count()

        segunda_extensao = primeira_extensao + timedelta(days=14)
        self._prorrogar(contrato, segunda_extensao)
        contrato.refresh_from_db()

        self.assertEqual(contrato.data_devolucao_prevista, segunda_extensao.replace(second=0, microsecond=0))
        self.assertGreater(
            ParcelaContrato.objects.filter(contrato=contrato).count(), qtd_apos_primeira
        )


class DiasPrevistosRetroativoTests(ContratosTestBase):
    """Cobre Contrato._dias_previstos(): contrato retroativo nao deve contar
    o periodo pre-cadastro no calculo do valor operacional. Fase 7 do plano
    de correcao do laudo tecnico financeiro (achado original desta auditoria)."""

    def test_retroativo_ativo_conta_so_do_cadastro_em_diante(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            origem_retroativa=True,
            data_saida=agora - timedelta(days=180),  # retroativo ha 6 meses
            data_devolucao_prevista=agora + timedelta(days=10),
        )

        dias = contrato._dias_previstos()

        # Deveria contar so do cadastro (~agora) ate a devolucao prevista
        # (~10 dias) -- nunca os ~190 dias do periodo retroativo inteiro.
        self.assertEqual(dias, 10)

    def test_normal_com_data_saida_continua_usando_data_saida(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            origem_retroativa=False,
            data_saida=agora - timedelta(days=3),
            data_devolucao_prevista=agora + timedelta(days=4),
        )

        dias = contrato._dias_previstos()

        # Comportamento preservado: conta desde a saida real (7 dias no total)
        self.assertEqual(dias, 7)

    def test_total_geral_retroativo_ativo_nao_infla_com_periodo_historico(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            origem_retroativa=True,
            diaria=Decimal('100.00'),
            data_saida=agora - timedelta(days=180),
            data_devolucao_prevista=agora + timedelta(days=10),
        )

        # total_locacao = diaria * dias -- com o fix, dias ~= 10, nao ~= 190
        self.assertLess(contrato.total_locacao, Decimal('100.00') * 30)

    def test_contrato_encerrado_retroativo_nao_e_afetado(self):
        """Contratos ja com total_dias fixado (pos check-in) nunca chamam
        _dias_previstos() -- o fix nao altera nenhum contrato ja encerrado."""
        agora = timezone.now()
        contrato = self.criar_contrato(
            origem_retroativa=True,
            diaria=Decimal('100.00'),
            data_saida=agora - timedelta(days=180),
            data_devolucao_prevista=agora - timedelta(days=170),
            total_dias=10,  # ja fixado no check-in, como um contrato real encerrado
        )

        self.assertEqual(contrato.total_locacao, Decimal('1000.00'))  # 100 * 10, usa total_dias


class ContratoEncerramentoAntecipadoTests(ContratosTestBase):
    """Cobre ContratoEncerramentoAntecipadoView: quebra de contrato com o
    veiculo ainda 'ativo' (devolucao antes do prazo previsto). Parcelas pagas
    permanecem intocadas, parcelas vencidas em aberto continuam em cobranca,
    e so as parcelas futuras (vencimento apos a devolucao real) sao
    canceladas -- nunca excluidas."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser('gestor', 'gestor@teste.com', 'senha123')
        self.client.force_login(self.user)

    def _criar_contrato_ativo(self, dias_uso=5, dias_contratados=30, **kwargs):
        agora = timezone.now()
        dados = dict(
            situacao='ativo',
            data_saida=agora - timedelta(days=dias_uso),
            km_saida=1000,
            combustivel_saida='cheio',
            data_devolucao_prevista=agora - timedelta(days=dias_uso) + timedelta(days=dias_contratados),
        )
        dados.update(kwargs)
        return self.criar_contrato(**dados)

    def _encerrar_antecipadamente(self, contrato, **overrides):
        agora = timezone.now()
        dados = {
            'data_devolucao_real': timezone.localtime(agora).strftime('%Y-%m-%dT%H:%M'),
            'km_devolucao': (contrato.km_saida or 0) + 100,
            'combustivel_devolucao': 'cheio',
            'obs_devolucao': '',
            'motivo_encerramento': 'cliente_solicitou',
            'motivo_encerramento_detalhe': '',
        }
        dados.update(overrides)
        return self.client.post(
            reverse('contratos:encerramento-antecipado', kwargs={'pk': contrato.pk}), dados,
        )

    def _criar_parcela(self, contrato, numero, dias_relativos, situacao, **kwargs):
        hoje = timezone.now().date()
        dados = dict(
            contrato=contrato, numero=numero, tipo='semanal',
            data_vencimento=hoje + timedelta(days=dias_relativos),
            valor=Decimal('700.00'), situacao=situacao,
        )
        dados.update(kwargs)
        return ParcelaContrato.objects.create(**dados)

    def test_parcela_paga_permanece_intocada(self):
        contrato = self._criar_contrato_ativo()
        paga = self._criar_parcela(contrato, 1, -5, 'pago', data_pagamento=contrato.data_saida)

        self._encerrar_antecipadamente(contrato)

        paga.refresh_from_db()
        self.assertEqual(paga.situacao, 'pago')

    def test_parcela_vencida_em_aberto_e_mantida_para_cobranca(self):
        contrato = self._criar_contrato_ativo()
        vencida = self._criar_parcela(contrato, 1, -3, 'em_atraso')

        self._encerrar_antecipadamente(contrato)

        vencida.refresh_from_db()
        self.assertEqual(vencida.situacao, 'em_atraso')

    def test_parcela_pendente_ja_vencida_tambem_e_mantida_mesmo_sem_flag_em_atraso(self):
        """O corte usa a data de vencimento, nao o campo 'situacao' -- uma
        parcela 'pendente' cujo vencimento ja passou (a rotina diaria ainda
        nao rodou para marca-la 'em_atraso') tambem deve ser mantida, nunca
        cancelada como se fosse futura."""
        contrato = self._criar_contrato_ativo()
        vencida_pendente = self._criar_parcela(contrato, 1, -1, 'pendente')

        self._encerrar_antecipadamente(contrato)

        vencida_pendente.refresh_from_db()
        self.assertEqual(vencida_pendente.situacao, 'pendente')

    def test_parcelas_futuras_sao_canceladas_com_motivo_registrado(self):
        contrato = self._criar_contrato_ativo()
        futura = self._criar_parcela(contrato, 1, 7, 'pendente')

        self._encerrar_antecipadamente(contrato, motivo_encerramento='inadimplencia')

        futura.refresh_from_db()
        self.assertEqual(futura.situacao, 'cancelada')
        self.assertIn('encerramento antecipado', futura.observacoes.lower())
        self.assertIn('Inadimplência', futura.observacoes)

    def test_parcela_futura_cancelada_nunca_e_excluida_do_banco(self):
        contrato = self._criar_contrato_ativo()
        futura = self._criar_parcela(contrato, 1, 7, 'pendente')

        self._encerrar_antecipadamente(contrato)

        self.assertTrue(ParcelaContrato.objects.filter(pk=futura.pk).exists())

    def test_fechamento_e_recalculado_com_a_devolucao_real(self):
        """Contrato de 30 dias devolvido no 5o dia: total_dias deve refletir
        o uso real, nao os 30 dias originalmente contratados -- senao o
        cliente seria cobrado pelo periodo inteiro mesmo devolvendo antes."""
        contrato = self._criar_contrato_ativo(dias_uso=5, dias_contratados=30)

        self._encerrar_antecipadamente(contrato)
        contrato.refresh_from_db()

        self.assertIsNotNone(contrato.total_dias)
        self.assertLess(contrato.total_dias, 30)
        self.assertLessEqual(contrato.total_dias, 6)

    def test_veiculo_e_liberado_apos_encerramento(self):
        contrato = self._criar_contrato_ativo()
        contrato.veiculo.situacao = 'em_uso'
        contrato.veiculo.save()

        self._encerrar_antecipadamente(contrato)

        contrato.veiculo.refresh_from_db()
        self.assertEqual(contrato.veiculo.situacao, 'disponivel')

    def test_contrato_e_marcado_como_encerramento_antecipado(self):
        contrato = self._criar_contrato_ativo()

        self._encerrar_antecipadamente(contrato, motivo_encerramento='problema_mecanico')
        contrato.refresh_from_db()

        self.assertEqual(contrato.situacao, 'encerrado')
        self.assertTrue(contrato.encerramento_antecipado)
        self.assertEqual(contrato.motivo_encerramento, 'problema_mecanico')
        self.assertEqual(contrato.encerrado_por_id, self.user.id)
        self.assertIsNotNone(contrato.encerrado_em)

    def test_caucao_e_avaliada_igual_ao_encerramento_normal(self):
        """Reaproveita a mesma regra de retencao de caucao do ContratoEncerrarView
        (_avaliar_caucao_no_encerramento): reter so o valor efetivo da avaria
        e devolver o excedente automaticamente."""
        contrato = self._criar_contrato_ativo(
            caucao_valor=Decimal('1000.00'), caucao_situacao='retido',
        )
        contrato.avarias.create(
            descricao='Para-choque arranhado', valor_cobrado=Decimal('200.00'), situacao='cobrada',
        )

        self._encerrar_antecipadamente(contrato)
        contrato.refresh_from_db()

        pagamento = PagamentoContrato.objects.get(contrato=contrato, tipo='avaria')
        self.assertEqual(pagamento.valor, Decimal('200.00'))
        self.assertEqual(contrato.caucao_situacao, 'devolvido_parcial')

    def test_historico_contrato_registra_snapshot_da_decisao(self):
        from .models import HistoricoContrato

        contrato = self._criar_contrato_ativo()
        self._criar_parcela(contrato, 1, -3, 'pago', data_pagamento=contrato.data_saida)
        self._criar_parcela(contrato, 2, 7, 'pendente')
        self._criar_parcela(contrato, 3, 14, 'pendente')

        self._encerrar_antecipadamente(contrato, motivo_encerramento='cliente_solicitou')
        contrato.refresh_from_db()

        historico = HistoricoContrato.objects.get(contrato=contrato)
        self.assertEqual(historico.acao, 'encerramento_antecipado')
        self.assertEqual(historico.situacao_anterior, 'ativo')
        self.assertEqual(historico.situacao_nova, 'encerrado')
        self.assertEqual(historico.usuario_id, self.user.id)
        self.assertEqual(historico.dados['parcelas_canceladas_qtd'], 2)

    def test_segundo_submit_apos_encerrado_nao_reprocessa(self):
        """Uma vez 'encerrado', o contrato deixa de casar com o filtro
        situacao='ativo' da view -- um duplo clique/reenvio cai em 404 e nao
        duplica cancelamentos nem o registro de auditoria."""
        from .models import HistoricoContrato

        contrato = self._criar_contrato_ativo()
        self._criar_parcela(contrato, 1, 7, 'pendente')

        primeira = self._encerrar_antecipadamente(contrato)
        self.assertEqual(primeira.status_code, 302)

        segunda = self._encerrar_antecipadamente(contrato)
        self.assertEqual(segunda.status_code, 404)

        self.assertEqual(HistoricoContrato.objects.filter(contrato=contrato).count(), 1)

    def test_bloqueia_usuario_sem_permissao_admin_locadora(self):
        from django.contrib.auth.models import Group

        atendente = User.objects.create_user('atendente1', 'atendente1@teste.com', 'senha123')
        atendente.groups.add(Group.objects.create(name='atendente'))
        self.client.force_login(atendente)

        contrato = self._criar_contrato_ativo()
        resp = self._encerrar_antecipadamente(contrato)

        contrato.refresh_from_db()
        self.assertEqual(contrato.situacao, 'ativo')
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('encerramento-antecipado', resp.url)

    def test_bloqueia_data_devolucao_no_futuro(self):
        contrato = self._criar_contrato_ativo()
        amanha = timezone.localtime(timezone.now() + timedelta(days=1))

        resp = self._encerrar_antecipadamente(
            contrato, data_devolucao_real=amanha.strftime('%Y-%m-%dT%H:%M'),
        )

        self.assertEqual(resp.status_code, 200)
        contrato.refresh_from_db()
        self.assertEqual(contrato.situacao, 'ativo')

    def test_exige_detalhe_quando_motivo_e_outro(self):
        contrato = self._criar_contrato_ativo()

        resp = self._encerrar_antecipadamente(
            contrato, motivo_encerramento='outro', motivo_encerramento_detalhe='',
        )

        self.assertEqual(resp.status_code, 200)
        contrato.refresh_from_db()
        self.assertEqual(contrato.situacao, 'ativo')

    def test_bloqueia_contrato_que_nao_esta_ativo(self):
        contrato = self._criar_contrato_ativo(situacao='aberto')

        resp = self._encerrar_antecipadamente(contrato)

        self.assertEqual(resp.status_code, 404)
