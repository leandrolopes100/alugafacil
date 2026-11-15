from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.customers.models import CNHCliente, Cliente
from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo
from apps.financeiro.models import ConfiguracaoLocadora, DespesaOperacional

from .models import Contrato, ParcelaContrato, PagamentoContrato, Reserva, gerar_parcelas
from .views import _checklist_saida


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

    def test_contrato_curto_gera_uma_parcela_proporcional_no_inicio(self):
        contrato = self.criar_contrato()
        inicio = timezone.localdate()
        fim = inicio + timedelta(days=3)

        qtd = gerar_parcelas(contrato, inicio, fim)

        self.assertEqual(qtd, 1)
        parcela = contrato.parcelas.get()
        self.assertEqual(parcela.data_vencimento, inicio)
        self.assertEqual(parcela.valor, contrato.diaria * 3)

    def test_contrato_de_sete_dias_gera_uma_parcela_no_inicio(self):
        contrato = self.criar_contrato()
        inicio = timezone.localdate()
        fim = inicio + timedelta(days=7)

        qtd = gerar_parcelas(contrato, inicio, fim)

        self.assertEqual(qtd, 1)
        parcela = contrato.parcelas.get()
        self.assertEqual(parcela.data_vencimento, inicio)
        self.assertEqual(parcela.valor, contrato.diaria * 7)

    def test_contrato_de_trinta_dias_gera_cinco_parcelas_semanais(self):
        contrato = self.criar_contrato()
        inicio = timezone.localdate()
        fim = inicio + timedelta(days=30)

        qtd = gerar_parcelas(contrato, inicio, fim)

        self.assertEqual(qtd, 5)
        vencimentos = list(
            contrato.parcelas.order_by('numero').values_list('data_vencimento', flat=True)
        )
        self.assertEqual(vencimentos, [inicio + timedelta(days=7 * i) for i in range(5)])

    def test_numeracao_continua_em_prorrogacao(self):
        contrato = self.criar_contrato()
        inicio = timezone.localdate()
        meio = inicio + timedelta(days=7)
        fim = meio + timedelta(days=7)

        gerar_parcelas(contrato, inicio, meio, origem='original')
        gerar_parcelas(contrato, meio, fim, origem='prorrogacao')

        parcelas = list(contrato.parcelas.order_by('numero'))
        self.assertEqual([p.numero for p in parcelas], [1, 2])
        self.assertEqual([p.origem for p in parcelas], ['original', 'prorrogacao'])

    def test_periodo_invalido_nao_gera_parcelas(self):
        contrato = self.criar_contrato()
        hoje = timezone.localdate()

        self.assertEqual(gerar_parcelas(contrato, hoje, hoje), 0)
        self.assertEqual(gerar_parcelas(contrato, hoje, hoje - timedelta(days=1)), 0)
        self.assertEqual(contrato.parcelas.count(), 0)

    def test_ultima_parcela_semanal_e_proporcional_ao_ciclo_parcial(self):
        """Fase 8 do plano de correcao do laudo: 10 dias = 1 semana cheia +
        3 dias -- a ultima parcela deve cobrar so os 3 dias, nao a semana
        inteira (achado 7 do laudo tecnico financeiro)."""
        contrato = self.criar_contrato()
        inicio = timezone.localdate()
        fim = inicio + timedelta(days=10)

        qtd = gerar_parcelas(contrato, inicio, fim)

        self.assertEqual(qtd, 2)
        parcelas = list(contrato.parcelas.order_by('numero'))
        self.assertEqual(parcelas[0].valor, contrato.diaria * 7)
        self.assertEqual(parcelas[1].valor, contrato.diaria * 3)

    def test_periodo_multiplo_exato_de_semana_continua_cobrando_semana_cheia(self):
        contrato = self.criar_contrato()
        inicio = timezone.localdate()
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
            data_vencimento=timezone.localdate() - timedelta(days=4),
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
            data_vencimento=timezone.localdate() - timedelta(days=2),
            valor=Decimal('500.00'), situacao='em_atraso',
        )

        self.assertEqual(parcela.valor_corrigido, Decimal('500.00'))

    def test_em_atraso_e_dias_atraso_para_pendente_vencida(self):
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.localdate() - timedelta(days=1),
            valor=Decimal('100.00'), situacao='pendente',
        )

        self.assertTrue(parcela.em_atraso)
        self.assertEqual(parcela.dias_atraso, 1)


class ChecklistCheckoutTests(ContratosTestBase):
    """Cobre _checklist_saida — a regra que decide se o veículo
    pode ou não ser liberado ao cliente (núcleo do fluxo operacional)."""

    def _falhas(self, contrato):
        return [msg for ok, msg in _checklist_saida(contrato) if ok is False]

    def _avisos(self, contrato):
        return [msg for ok, msg in _checklist_saida(contrato) if ok is None]

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
            categoria='b', validade=timezone.localdate() - timedelta(days=10),
        )
        contrato = self.criar_contrato(caucao_valor=Decimal('500.00'), caucao_situacao='pendente')

        falhas = self._falhas(contrato)

        self.assertTrue(any('CNH vencida' in m for m in falhas))
        self.assertTrue(any('Caução' in m and 'não registrado' in m for m in falhas))

    def test_avisa_sem_bloquear_quando_primeira_parcela_nao_foi_paga(self):
        CNHCliente.objects.create(
            cliente=self.cliente, numero='12345678900', estado_emissor='SP',
            categoria='b', validade=timezone.localdate() + timedelta(days=365),
        )
        contrato = self.criar_contrato(caucao_valor=Decimal('0.00'))

        self.assertTrue(any('Primeira parcela' in m for m in self._avisos(contrato)))
        self.assertFalse(any('Primeira parcela' in m for m in self._falhas(contrato)))

    def test_aprova_checklist_quando_tudo_esta_em_ordem(self):
        CNHCliente.objects.create(
            cliente=self.cliente, numero='12345678900', estado_emissor='SP',
            categoria='b', validade=timezone.localdate() + timedelta(days=365),
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
            data_vencimento=timezone.localdate() + timedelta(days=1),
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
            data_vencimento=timezone.localdate() - timedelta(days=4),
            valor=Decimal('700.00'), situacao='em_atraso',
        )
        valor_corrigido = parcela.valor_corrigido  # inclui multa + juros

        self._pagar(contrato, parcela, valor_corrigido)
        parcela.refresh_from_db()

        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(PagamentoContrato.objects.get(contrato=contrato).valor, valor_corrigido)

    def _parcela_atrasada(self):
        ConfiguracaoLocadora.objects.create(
            pk=1, percentual_multa_atraso=Decimal('2.00'),
            percentual_juros_diario=Decimal('0.10'), dias_carencia=0,
        )
        contrato = self.criar_contrato(situacao='ativo', data_saida=timezone.now() - timedelta(days=4))
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1,
            data_vencimento=timezone.localdate() - timedelta(days=4),
            valor=Decimal('700.00'), situacao='em_atraso',
        )
        return contrato, parcela

    def _abertas(self, contrato):
        return list(contrato.parcelas.filter(situacao__in=['pendente', 'em_atraso']))

    def test_valor_menor_vira_pagamento_parcial_e_restante_continua_em_cobranca(self):
        contrato, parcela = self._parcela_atrasada()
        devido = parcela.valor_corrigido

        self._pagar(contrato, parcela, Decimal('700.00'), observacoes='pagou parte')

        abertas = self._abertas(contrato)
        self.assertEqual(len(abertas), 1)
        self.assertEqual(abertas[0].valor_corrigido, devido - Decimal('700.00'))
        self.assertEqual(PagamentoContrato.objects.get(contrato=contrato).valor, Decimal('700.00'))
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(contrato.saldo_devedor, abertas[0].valor)
        self.assertEqual(abertas[0].valor + parcela.__class__.objects.get(pk=parcela.pk).valor, Decimal('700.00'))

    def test_desconto_sem_motivo_e_rejeitado(self):
        contrato, parcela = self._parcela_atrasada()
        self.client.post(
            reverse('contratos:parcela-pagar', kwargs={'pk': contrato.pk, 'parcela_pk': parcela.pk}),
            {'valor': '700.00', 'forma_pagamento': 'pix', 'desconto': '1'},
        )
        parcela.refresh_from_db()
        self.assertEqual(parcela.situacao, 'em_atraso')
        self.assertFalse(PagamentoContrato.objects.filter(contrato=contrato).exists())

    def test_desconto_com_motivo_quita_a_parcela_pelo_valor_informado(self):
        contrato, parcela = self._parcela_atrasada()
        self.client.post(
            reverse('contratos:parcela-pagar', kwargs={'pk': contrato.pk, 'parcela_pk': parcela.pk}),
            {'valor': '700.00', 'forma_pagamento': 'pix', 'desconto': '1',
             'observacoes': 'Cliente negociou dispensa de multa com a gerência.'},
        )
        parcela.refresh_from_db()
        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(self._abertas(contrato), [])
        self.assertEqual(PagamentoContrato.objects.get(contrato=contrato).valor, Decimal('700.00'))
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(contrato.saldo_devedor, Decimal('0.00'))

    def test_desconto_de_atendente_fica_registrado_no_historico(self):
        from django.contrib.auth.models import Group
        from .models import HistoricoContrato
        atendente = User.objects.create_user('atendente_desconto', password='senha123')
        atendente.groups.add(Group.objects.get_or_create(name='atendente')[0])
        self.client.force_login(atendente)
        contrato, parcela = self._parcela_atrasada()
        devido = parcela.valor_corrigido

        self.client.post(
            reverse('contratos:parcela-pagar', kwargs={'pk': contrato.pk, 'parcela_pk': parcela.pk}),
            {'valor': '1.00', 'forma_pagamento': 'pix', 'desconto': '1', 'observacoes': 'Acordo com o cliente.'},
        )

        parcela.refresh_from_db()
        self.assertEqual(parcela.situacao, 'pago')
        registro = HistoricoContrato.objects.get(contrato=contrato, acao='desconto')
        self.assertEqual(registro.usuario, atendente)
        self.assertEqual(registro.motivo, 'Acordo com o cliente.')
        self.assertEqual(Decimal(registro.dados['valor_devido']), devido)
        self.assertEqual(Decimal(registro.dados['valor_recebido']), Decimal('1.00'))
        self.assertEqual(Decimal(registro.dados['desconto']), devido - Decimal('1.00'))
        self.assertEqual(registro.dados['parcela_id'], parcela.pk)

    def test_pagamento_parcial_nao_gera_registro_de_desconto(self):
        from .models import HistoricoContrato
        contrato, parcela = self._parcela_atrasada()
        self._pagar(contrato, parcela, Decimal('300.00'))
        self.assertFalse(HistoricoContrato.objects.filter(contrato=contrato, acao='desconto').exists())

    def test_valor_maior_aplica_excedente_na_proxima_parcela(self):
        contrato = self.criar_contrato()
        hoje = timezone.localdate()
        p1 = ParcelaContrato.objects.create(
            contrato=contrato, numero=1, data_vencimento=hoje, valor=Decimal('700.00'),
        )
        p2 = ParcelaContrato.objects.create(
            contrato=contrato, numero=2, data_vencimento=hoje + timedelta(days=7), valor=Decimal('700.00'),
        )
        self._pagar(contrato, p1, Decimal('1000.00'))
        p1.refresh_from_db()
        self.assertEqual(p1.situacao, 'pago')
        self.assertEqual(p1.pagamento_vinculado.valor, Decimal('700.00'))
        abertas = self._abertas(contrato)
        self.assertEqual(sum(p.valor for p in abertas), Decimal('400.00'))
        self.assertEqual(
            PagamentoContrato.objects.filter(contrato=contrato).aggregate(s=Sum('valor'))['s'],
            Decimal('1000.00'),
        )


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
            data_vencimento=timezone.localdate() + timedelta(days=1),
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
            data_vencimento=timezone.localdate(), valor=Decimal('700.00'), situacao='pendente',
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

    def test_reverter_volta_km_do_veiculo_e_apaga_km_da_devolucao(self):
        from apps.fleet.models import HistoricoKmVeiculo
        contrato, agora = self._contrato_e_agora_com_excedente()
        self._checkin(contrato, agora)

        self._reverter(contrato)

        self.veiculo.refresh_from_db()
        self.assertEqual(self.veiculo.km_atual, 10000)
        self.assertFalse(HistoricoKmVeiculo.objects.filter(
            contrato=contrato, origem='contrato_devolucao'
        ).exists())
        self.assertTrue(HistoricoKmVeiculo.objects.filter(
            contrato=contrato, origem='contrato_saida', km=10000
        ).exists())

    def test_refazer_checkin_grava_o_km_novo_no_historico(self):
        from apps.fleet.models import HistoricoKmVeiculo
        contrato, agora = self._contrato_e_agora_com_excedente()
        self._checkin(contrato, agora)
        self._reverter(contrato)

        self._checkin(contrato, agora, km_devolucao=12600)

        devolucao = HistoricoKmVeiculo.objects.get(contrato=contrato, origem='contrato_devolucao')
        self.assertEqual(devolucao.km, 12600)

    def test_reverter_respeita_km_maior_registrado_por_outra_origem(self):
        from apps.fleet.models import HistoricoKmVeiculo
        contrato, agora = self._contrato_e_agora_com_excedente()
        self._checkin(contrato, agora)
        HistoricoKmVeiculo.objects.create(veiculo=self.veiculo, km=12700, origem='manutencao')

        self._reverter(contrato)

        self.veiculo.refresh_from_db()
        self.assertEqual(self.veiculo.km_atual, 12700)


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
        hoje = timezone.localdate()
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


class FusoHorarioTests(ContratosTestBase):
    """Entre 21h e 0h em Brasília o UTC já está no dia seguinte."""

    NOITE_BRASILIA = datetime(2026, 11, 2, 23, 30, tzinfo=ZoneInfo('America/Sao_Paulo'))

    def test_parcela_que_vence_hoje_nao_esta_em_atraso_a_noite(self):
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1, valor=Decimal('700.00'),
            data_vencimento=date(2026, 11, 2),
        )

        with patch('django.utils.timezone.now', return_value=self.NOITE_BRASILIA):
            self.assertFalse(parcela.em_atraso)
            self.assertEqual(parcela.dias_atraso, 0)
            self.assertEqual(parcela.valor_corrigido, parcela.valor)

    def test_tarefa_noturna_nao_marca_atraso_de_parcela_que_vence_hoje(self):
        from apps.core.tasks import marcar_parcelas_atrasadas
        contrato = self.criar_contrato()
        parcela = ParcelaContrato.objects.create(
            contrato=contrato, numero=1, valor=Decimal('700.00'),
            data_vencimento=date(2026, 11, 2),
        )

        with patch('django.utils.timezone.now', return_value=self.NOITE_BRASILIA):
            marcar_parcelas_atrasadas()

        parcela.refresh_from_db()
        self.assertEqual(parcela.situacao, 'pendente')

    def test_saida_a_noite_registra_conta_a_receber_no_dia_de_brasilia(self):
        from apps.financeiro.models import ContaReceber
        saida_utc = self.NOITE_BRASILIA.astimezone(ZoneInfo('UTC'))
        contrato = self.criar_contrato(
            situacao='ativo',
            data_saida=saida_utc,
            data_devolucao_prevista=saida_utc + timedelta(days=14),
        )

        conta = ContaReceber.objects.get(contrato=contrato)
        self.assertEqual(conta.data_emissao, date(2026, 11, 2))
        self.assertEqual(conta.data_vencimento, date(2026, 11, 16))


class EncerrarMantemAcertoTests(ContratosTestBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser('gestor_acerto', 'ac@teste.com', 'senha123'))
        self.contrato = self.criar_contrato(situacao='aguardando_devolucao', data_saida=timezone.now())
        self.acerto = ParcelaContrato.objects.create(
            contrato=self.contrato, numero=5, tipo='acerto', valor=Decimal('180.00'),
            data_vencimento=timezone.localdate(), observacoes='120 km excedente',
        )

    def encerrar(self):
        return self.client.post(reverse('contratos:encerrar', args=[self.contrato.pk]), follow=True)

    def test_encerrar_mantem_parcela_de_acerto_em_aberto(self):
        resposta = self.encerrar()

        self.contrato.refresh_from_db()
        self.acerto.refresh_from_db()
        self.assertEqual(self.contrato.situacao, 'encerrado')
        self.assertEqual(self.acerto.situacao, 'pendente')
        self.assertContains(resposta, 'continua em aberto na Agenda de Cobranças')

    def test_acerto_continua_na_agenda_depois_de_encerrar(self):
        self.encerrar()

        resposta = self.client.get(reverse('financeiro:agenda'))

        self.assertIn(self.acerto, resposta.context['esta_semana'])

    def test_encerrar_ainda_cancela_parcela_de_locacao_em_aberto(self):
        locacao = ParcelaContrato.objects.create(
            contrato=self.contrato, numero=4, valor=Decimal('700.00'),
            data_vencimento=timezone.localdate() + timedelta(days=3),
        )

        self.encerrar()

        locacao.refresh_from_db()
        self.assertEqual(locacao.situacao, 'cancelada')

    def test_janela_de_encerramento_avisa_que_acerto_continua(self):
        resposta = self.client.get(reverse('contratos:detalhe', args=[self.contrato.pk]))

        self.assertContains(resposta, 'continua <strong>em aberto</strong> na Agenda de Cobranças')
        self.assertNotContains(resposta, 'serão <strong>canceladas</strong> ao encerrar')


class RegraUnicaDoTotalTests(ContratosTestBase):
    """Total do contrato = o que foi cobrado em parcelas + avarias + lançamentos avulsos."""

    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser('gestor_total', 'tot@teste.com', 'senha123'))
        self.agora = timezone.now()

    def contrato_ativo(self, dias_desde_saida, dias_ate_prevista, **kwargs):
        contrato = self.criar_contrato(
            situacao='ativo',
            data_saida=self.agora - timedelta(days=dias_desde_saida),
            data_devolucao_prevista=self.agora + timedelta(days=dias_ate_prevista),
            km_saida=10000, combustivel_saida='cheio', **kwargs,
        )
        gerar_parcelas(
            contrato, timezone.localdate(contrato.data_saida),
            timezone.localdate(contrato.data_devolucao_prevista),
        )
        return contrato

    def pagar(self, parcela, valor=None):
        parcela.situacao = 'pago'
        parcela.data_pagamento = self.agora
        parcela.save()
        PagamentoContrato.objects.create(
            contrato=parcela.contrato, parcela=parcela, forma_pagamento='pix',
            tipo='locacao', valor=valor or parcela.valor, data_pagamento=self.agora,
        )

    def checkin(self, contrato, km=10100):
        return self.client.post(reverse('contratos:checkin', args=[contrato.pk]), {
            'data_devolucao_real': timezone.localtime(self.agora).strftime('%Y-%m-%dT%H:%M'),
            'km_devolucao': str(km), 'combustivel_devolucao': 'cheio', 'obs_devolucao': '',
            'motivo_encerramento': 'cliente_solicitou',
        })

    def recarregar(self, contrato):
        return Contrato.objects.get(pk=contrato.pk)

    def test_conta_a_receber_bate_com_as_parcelas_emitidas(self):
        from apps.financeiro.models import ContaReceber
        contrato = self.contrato_ativo(0, 14)

        conta = ContaReceber.objects.get(contrato=contrato)

        self.assertEqual(conta.valor_total, Decimal('1400.00'))
        self.assertEqual(self.recarregar(contrato).total_geral, Decimal('1400.00'))

    def test_dias_extras_nao_sao_cobrados_em_dobro(self):
        contrato = self.contrato_ativo(10, -3)
        self.pagar(contrato.parcelas.get())

        self.checkin(contrato)

        contrato = self.recarregar(contrato)
        acerto = contrato.parcelas.get(tipo='acerto')
        self.assertEqual(contrato.dias_extras, 3)
        self.assertEqual(acerto.valor, Decimal('300.00'))
        self.assertEqual(contrato.total_locacao, Decimal('1000.00'))
        self.assertEqual(contrato.total_geral, Decimal('1000.00'))
        self.assertEqual(contrato.saldo_devedor, Decimal('300.00'))

    def test_multa_e_juros_pagos_entram_no_total_sem_gerar_credito(self):
        contrato = self.contrato_ativo(10, 4)
        primeira, segunda = contrato.parcelas.order_by('numero')

        self.pagar(primeira, Decimal('735.00'))

        contrato = self.recarregar(contrato)
        self.assertEqual(contrato.total_geral, Decimal('735.00') + segunda.valor)
        self.assertEqual(contrato.saldo_devedor, segunda.valor)
        self.assertIn(('Multa/juros de atraso', Decimal('35.00')), contrato.composicao_financeira)

    def test_regularizacao_retroativa_nao_gera_credito(self):
        contrato = self.contrato_ativo(0, 7, origem_retroativa=True)
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='dinheiro', tipo='regularizacao',
            valor=Decimal('1500.00'), data_pagamento=self.agora,
        )

        contrato = self.recarregar(contrato)

        self.assertEqual(contrato.saldo_devedor, Decimal('700.00'))

    def test_adicionais_entram_nas_parcelas_geradas(self):
        contrato = self.criar_contrato(
            situacao='ativo', data_saida=self.agora,
            data_devolucao_prevista=self.agora + timedelta(days=14),
        )
        contrato.adicionais.create(tipo='gps', diaria=Decimal('10.00'), quantidade=1)

        gerar_parcelas(contrato, timezone.localdate(), timezone.localdate() + timedelta(days=14))

        self.assertEqual(
            list(contrato.parcelas.order_by('numero').values_list('valor', 'dias')),
            [(Decimal('770.00'), 7), (Decimal('770.00'), 7)],
        )

    def test_adicional_em_contrato_ativo_reprecifica_so_parcelas_futuras(self):
        contrato = self.contrato_ativo(3, 18)

        self.client.post(reverse('contratos:adicionais', args=[contrato.pk]), {
            'tipo': 'gps', 'descricao': '', 'diaria': '10.00', 'quantidade': '1',
        })

        valores = list(contrato.parcelas.order_by('numero').values_list('valor', flat=True))
        self.assertEqual(valores, [Decimal('700.00'), Decimal('770.00'), Decimal('770.00')])
        self.assertEqual(self.recarregar(contrato).total_geral, Decimal('2240.00'))

    def test_adicional_bloqueado_depois_do_checkin(self):
        contrato = self.criar_contrato(situacao='aguardando_devolucao', data_saida=self.agora)

        self.client.post(reverse('contratos:adicionais', args=[contrato.pk]), {
            'tipo': 'gps', 'descricao': '', 'diaria': '10.00', 'quantidade': '1',
        })

        self.assertFalse(contrato.adicionais.exists())

    def test_checkin_antecipado_cancela_so_parcelas_futuras_sem_credito(self):
        contrato = self.contrato_ativo(10, 18)
        for parcela in contrato.parcelas.filter(data_vencimento__lte=timezone.localdate()):
            self.pagar(parcela)

        self.checkin(contrato)

        contrato = self.recarregar(contrato)
        self.assertEqual(contrato.situacao, 'aguardando_devolucao')
        self.assertEqual(contrato.parcelas.filter(situacao='cancelada').count(), 2)
        self.assertEqual(contrato.total_geral, Decimal('1400.00'))
        self.assertEqual(contrato.saldo_devedor, Decimal('0.00'))

    def test_checkin_com_parcela_vencida_em_aberto_e_permitido_e_mantem_a_cobranca(self):
        contrato = self.contrato_ativo(10, 18)
        self.pagar(contrato.parcelas.order_by('numero').first())
        vencidas = list(contrato.parcelas_devidas_ate(timezone.localdate()).values_list('pk', flat=True))
        self.assertTrue(vencidas)

        self.checkin(contrato)

        contrato = self.recarregar(contrato)
        self.assertEqual(contrato.situacao, 'aguardando_devolucao')
        self.assertEqual(
            set(contrato.parcelas_abertas().filter(pk__in=vencidas).values_list('pk', flat=True)),
            set(vencidas),
        )

    def test_reverter_checkin_restaura_parcelas_futuras(self):
        contrato = self.contrato_ativo(10, 18)
        for parcela in contrato.parcelas.filter(data_vencimento__lte=timezone.localdate()):
            self.pagar(parcela)
        self.checkin(contrato)

        self.client.post(reverse('contratos:reverter-checkin', args=[contrato.pk]))

        contrato = self.recarregar(contrato)
        self.assertEqual(contrato.situacao, 'ativo')
        self.assertEqual(contrato.parcelas.filter(situacao='pendente').count(), 2)
        self.assertFalse(contrato.parcelas.filter(observacoes__contains='cancelada no check-in').exists())
        self.assertEqual(contrato.total_geral, Decimal('2800.00'))

    def test_encerrar_mantem_parcela_vencida_em_cobranca(self):
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao', data_saida=self.agora - timedelta(days=10),
            data_devolucao_real=self.agora,
        )
        vencida = ParcelaContrato.objects.create(
            contrato=contrato, numero=1, valor=Decimal('700.00'),
            data_vencimento=timezone.localdate() - timedelta(days=3),
        )

        self.client.post(reverse('contratos:encerrar', args=[contrato.pk]))

        vencida.refresh_from_db()
        self.assertEqual(vencida.situacao, 'pendente')
        self.assertEqual(self.recarregar(contrato).saldo_devedor, Decimal('700.00'))


class TelasFinanceirasRenderizamTests(ContratosTestBase):
    """As telas que exibem totais do contrato renderizam sem erro em cada situação."""

    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser('gestor_fumaca', 'f@teste.com', 'senha123'))

    def test_telas_renderizam(self):
        from django.template.loader import render_to_string
        from apps.financeiro.models import ContaReceber
        agora = timezone.now()
        aberto = self.criar_contrato()
        ativo = self.criar_contrato(
            situacao='ativo', data_saida=agora - timedelta(days=10),
            data_devolucao_prevista=agora + timedelta(days=11), km_saida=1000,
            combustivel_saida='cheio',
        )
        gerar_parcelas(ativo, timezone.localdate(ativo.data_saida), timezone.localdate(ativo.data_devolucao_prevista))
        PagamentoContrato.objects.create(
            contrato=ativo, forma_pagamento='pix', tipo='multa', valor=Decimal('130.00'),
        )

        for url in (
            reverse('contratos:detalhe', args=[aberto.pk]),
            reverse('contratos:detalhe', args=[ativo.pk]),
            reverse('contratos:checkin', args=[ativo.pk]),
            reverse('contratos:pagamentos', args=[ativo.pk]),
            reverse('financeiro:conta-receber-detalhe', args=[ContaReceber.objects.get(contrato=ativo).pk]),
            reverse('core:dashboard'),
            reverse('core:relatorios'),
            reverse('core:relatorio-frota'),
            reverse('core:relatorio-dre'),
            reverse('core:relatorio-contratos'),
            reverse('core:relatorio-exportar', args=['inadimplencia']),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        html = render_to_string('contracts/pdf_contrato.html', {'contrato': Contrato.objects.get(pk=ativo.pk)})
        self.assertIn('Total da Loca', html)
        self.assertIn(('Multa de Trânsito', Decimal('130.00')), Contrato.objects.get(pk=ativo.pk).composicao_financeira)


class MigracaoRegraDoTotalTests(ContratosTestBase):

    def test_preenche_dias_e_reclassifica_regularizacao_antiga(self):
        import importlib
        from django.apps import apps as registro
        migracao = importlib.import_module('apps.contracts.migrations.0013_parcela_dias_pagamento_regularizacao')
        contrato = self.criar_contrato(situacao='ativo', data_saida=timezone.now(), origem_retroativa=True)
        semanal = ParcelaContrato.objects.create(
            contrato=contrato, numero=1, valor=Decimal('700.00'), data_vencimento=timezone.localdate(),
        )
        quebrada = ParcelaContrato.objects.create(
            contrato=contrato, numero=2, valor=Decimal('333.33'), data_vencimento=timezone.localdate(),
        )
        antiga = PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='dinheiro', tipo='locacao', valor=Decimal('900.00'),
            observacoes='Regularização de saldo pré-sistema — período de 01/01/2026 a 01/02/2026.',
        )

        migracao.preencher_dias_e_regularizacao(registro, None)

        semanal.refresh_from_db()
        quebrada.refresh_from_db()
        antiga.refresh_from_db()
        self.assertEqual(semanal.dias, 7)
        self.assertIsNone(quebrada.dias)
        self.assertEqual(antiga.tipo, 'regularizacao')


class CaucaoNoEncerramentoTests(ContratosTestBase):
    """O caução abate parcelas vencidas, acerto, avarias e multas de trânsito,
    nesta ordem, e o restante é devolvido na hora do encerramento."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser('gestor_caucao', 'c@teste.com', 'senha123')
        self.client.force_login(self.user)
        config = ConfiguracaoLocadora.obter()
        config.percentual_multa_atraso = Decimal('0')
        config.percentual_juros_diario = Decimal('0')
        config.save()
        self.hoje = timezone.localdate()

    def _contrato_com_caucao(self, valor_caucao):
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao', data_saida=timezone.now() - timedelta(days=20),
            data_devolucao_prevista=timezone.now() - timedelta(days=1),
            data_devolucao_real=timezone.now(), km_saida=1000, combustivel_saida='cheio',
            caucao_valor=valor_caucao, caucao_situacao='pago',
        )
        PagamentoContrato.objects.create(
            contrato=contrato, tipo='caucao', forma_pagamento='pix', valor=valor_caucao,
        )
        return contrato

    def _parcela(self, contrato, numero, dias, valor, **kwargs):
        return ParcelaContrato.objects.create(
            contrato=contrato, numero=numero, valor=Decimal(valor),
            data_vencimento=self.hoje + timedelta(days=dias), **kwargs,
        )

    def _multa(self, contrato, valor):
        from apps.financeiro.models import MultaTransito
        return MultaTransito.objects.create(
            veiculo=self.veiculo, contrato=contrato, data_infracao=self.hoje - timedelta(days=3),
            descricao='Excesso de velocidade', valor=Decimal(valor), situacao='identificada',
        )

    def _encerrar(self, contrato):
        return self.client.post(reverse('contratos:encerrar', args=[contrato.pk]))

    def test_abate_parcela_avaria_e_multa_e_devolve_o_resto(self):
        contrato = self._contrato_com_caucao(Decimal('1000.00'))
        parcela = self._parcela(contrato, 1, -5, '300.00')
        contrato.avarias.create(descricao='Retrovisor', valor_cobrado=Decimal('200.00'), situacao='cobrada')
        multa = self._multa(contrato, '150.00')

        self._encerrar(contrato)

        contrato = Contrato.objects.get(pk=contrato.pk)
        parcela.refresh_from_db()
        multa.refresh_from_db()
        abatidos = PagamentoContrato.objects.filter(contrato=contrato, forma_pagamento='abatido_caucao')
        self.assertEqual(
            sorted((p.tipo, p.valor) for p in abatidos),
            [('avaria', Decimal('200.00')), ('locacao', Decimal('300.00')), ('multa', Decimal('150.00'))],
        )
        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(multa.situacao, 'cobrada_cliente')
        self.assertFalse(contrato.avarias.filter(situacao='cobrada').exists())
        self.assertEqual(contrato.caucao_situacao, 'devolvido_parcial')
        devolucao = DespesaOperacional.objects.get(observacoes__contains=f'[caucao:{contrato.numero}]')
        self.assertEqual(devolucao.valor, Decimal('350.00'))
        self.assertEqual(contrato.saldo_devedor, Decimal('0.00'))

    def test_multa_sem_condutor_identificado_nao_e_abatida_e_fica_avisada(self):
        contrato = self._contrato_com_caucao(Decimal('500.00'))
        identificada = self._multa(contrato, '100.00')
        pendente = self._multa(contrato, '150.00')
        pendente.situacao = 'pendente_identificacao'
        pendente.save()

        plano = contrato.plano_abatimento_caucao()
        self.assertEqual([m.pk for m in plano['multas_sem_condutor']], [pendente.pk])
        self.assertEqual(self.client.get(reverse('contratos:detalhe', args=[contrato.pk])).status_code, 200)

        resposta = self._encerrar(contrato)

        identificada.refresh_from_db()
        pendente.refresh_from_db()
        self.assertEqual(identificada.situacao, 'cobrada_cliente')
        self.assertEqual(pendente.situacao, 'pendente_identificacao')
        self.assertIsNone(pendente.parcela_id)
        devolucao = DespesaOperacional.objects.get(observacoes__contains=f'[caucao:{contrato.numero}]')
        self.assertEqual(devolucao.valor, Decimal('400.00'))
        mensagens = [str(m) for m in resposta.wsgi_request._messages]
        self.assertTrue(any('sem condutor identificado' in m for m in mensagens), mensagens)

    def test_caucao_menor_que_a_divida_divide_a_parcela_e_mantem_o_restante(self):
        contrato = self._contrato_com_caucao(Decimal('500.00'))
        self._parcela(contrato, 1, -10, '400.00')
        segunda = self._parcela(contrato, 2, -3, '300.00')

        self._encerrar(contrato)

        contrato = Contrato.objects.get(pk=contrato.pk)
        segunda.refresh_from_db()
        restante = contrato.parcelas.get(situacao='pendente')
        self.assertEqual((segunda.valor, segunda.situacao), (Decimal('100.00'), 'pago'))
        self.assertEqual(restante.valor, Decimal('200.00'))
        self.assertEqual(restante.data_vencimento, segunda.data_vencimento)
        self.assertEqual(contrato.caucao_situacao, 'retido')
        self.assertFalse(DespesaOperacional.objects.filter(categoria='caucao').exists())
        self.assertEqual(contrato.saldo_devedor, Decimal('200.00'))
        self.assertEqual(contrato.conta_receber.valor_pago, Decimal('500.00'))

    def test_avaria_maior_que_o_caucao_fica_com_o_restante_cobrado(self):
        contrato = self._contrato_com_caucao(Decimal('1000.00'))
        avaria = contrato.avarias.create(descricao='Para-lama', valor_cobrado=Decimal('1500.00'), situacao='cobrada')

        self._encerrar(contrato)

        contrato = Contrato.objects.get(pk=contrato.pk)
        avaria.refresh_from_db()
        self.assertEqual((avaria.valor_cobrado, avaria.situacao), (Decimal('500.00'), 'cobrada'))
        self.assertEqual(contrato.total_avarias, Decimal('1500.00'))
        self.assertEqual(contrato.saldo_devedor, Decimal('500.00'))

    def test_caucao_sai_da_receita_e_abatimento_nao_entra_no_caixa(self):
        contrato = self._contrato_com_caucao(Decimal('1000.00'))
        self._parcela(contrato, 1, -1, '300.00')
        self._encerrar(contrato)
        periodo = {'ano': self.hoje.year, 'mes': self.hoje.month}

        dre = self.client.get(reverse('core:relatorio-dre'), periodo).context
        fluxo = self.client.get(reverse('financeiro:fluxo'), periodo).context
        dashboard = self.client.get(reverse('core:dashboard')).context

        self.assertEqual(dre['receita_total'], Decimal('300.00'))
        self.assertNotIn('caucao', {d['categoria'] for d in dre['despesa_por_cat']})
        self.assertEqual(dashboard['receita_mes'], Decimal('300.00'))
        self.assertEqual(dashboard['desp_pago'], Decimal('0.00'))
        self.assertEqual(fluxo['total_entradas'], Decimal('1000.00'))
        self.assertEqual(fluxo['total_saidas'], Decimal('700.00'))

    def test_caucao_nao_coletado_cancela_a_parcela_de_caucao(self):
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao', data_saida=timezone.now() - timedelta(days=5),
            data_devolucao_real=timezone.now(), caucao_valor=Decimal('500.00'), caucao_situacao='pendente',
        )
        parcela = self._parcela(contrato, 1, -5, '500.00', tipo='caucao')

        self._encerrar(contrato)

        parcela.refresh_from_db()
        self.assertEqual(parcela.situacao, 'cancelada')
        self.assertFalse(PagamentoContrato.objects.filter(forma_pagamento='abatido_caucao').exists())
        self.assertFalse(DespesaOperacional.objects.filter(categoria='caucao').exists())

    def test_janela_de_encerramento_mostra_a_divisao_do_caucao(self):
        contrato = self._contrato_com_caucao(Decimal('1000.00'))
        self._parcela(contrato, 1, -5, '300.00')

        resposta = self.client.get(reverse('contratos:detalhe', args=[contrato.pk]))

        plano = resposta.context['plano_caucao']
        self.assertEqual((plano['total_abatido'], plano['devolver']), (Decimal('300.00'), Decimal('700.00')))
        self.assertEqual(resposta.context['vencidas_mantidas_qtd'], 0)
        self.assertContains(resposta, 'Devolver ao cliente agora')

    def test_quebra_de_contrato_tambem_abate_do_caucao(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            situacao='ativo', data_saida=agora - timedelta(days=10), km_saida=1000,
            combustivel_saida='cheio', data_devolucao_prevista=agora + timedelta(days=20),
            caucao_valor=Decimal('1000.00'), caucao_situacao='pago',
        )
        PagamentoContrato.objects.create(
            contrato=contrato, tipo='caucao', forma_pagamento='pix', valor=Decimal('1000.00'),
        )
        parcela = self._parcela(contrato, 1, -3, '700.00')

        self.client.post(reverse('contratos:encerramento-antecipado', args=[contrato.pk]), {
            'data_devolucao_real': timezone.localtime(agora).strftime('%Y-%m-%dT%H:%M'),
            'km_devolucao': 1100, 'combustivel_devolucao': 'cheio', 'obs_devolucao': '',
            'motivo_encerramento': 'cliente_solicitou', 'motivo_encerramento_detalhe': '',
        })

        contrato = Contrato.objects.get(pk=contrato.pk)
        parcela.refresh_from_db()
        self.assertEqual(contrato.situacao, 'encerrado')
        self.assertEqual(parcela.forma_pagamento, 'abatido_caucao')
        self.assertEqual(contrato.caucao_situacao, 'devolvido_parcial')
        devolucao = DespesaOperacional.objects.get(observacoes__contains=f'[caucao:{contrato.numero}]')
        self.assertEqual(devolucao.valor, Decimal('300.00'))


class CaucaoSemEncargosTests(ContratosTestBase):
    """Parcela de caução não leva multa nem juros, e a régua não a chama de aluguel."""

    def test_caucao_atrasado_mantem_o_valor_original(self):
        from apps.financeiro.models import NotificacaoCobranca
        config = ConfiguracaoLocadora.obter()
        config.percentual_multa_atraso = Decimal('2')
        config.percentual_juros_diario = Decimal('1')
        config.dias_carencia = 0
        config.save()
        contrato = self.criar_contrato(situacao='ativo')
        hoje = timezone.localdate()
        caucao = ParcelaContrato.objects.create(
            contrato=contrato, numero=1, tipo='caucao', valor=Decimal('500.00'),
            data_vencimento=hoje - timedelta(days=10),
        )
        locacao = ParcelaContrato.objects.create(
            contrato=contrato, numero=2, valor=Decimal('500.00'),
            data_vencimento=hoje - timedelta(days=10),
        )

        mensagem = NotificacaoCobranca.mensagem(caucao, 'd_mais_7', 'http://link')

        self.assertEqual(caucao.valor_corrigido, Decimal('500.00'))
        self.assertGreater(locacao.valor_corrigido, Decimal('500.00'))
        self.assertNotIn('multa e juros', mensagem)
        self.assertNotIn('aluguel', mensagem)

    def test_forma_abatido_do_caucao_nao_aparece_para_o_operador(self):
        from .forms import CheckoutForm, PagamentoContratoForm
        from apps.financeiro.forms import RecebimentoForm
        for campo in (
            PagamentoContratoForm().fields['forma_pagamento'],
            CheckoutForm().fields['caucao_forma_pagamento'],
            RecebimentoForm().fields['forma_pagamento'],
        ):
            self.assertNotIn('abatido_caucao', dict(campo.choices))


from apps.financeiro.models import ContaReceber


class AbertosTestBase(ContratosTestBase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_superuser('gestor_abertos', 'a@teste.com', 'senha123')
        self.client.force_login(self.user)
        self.hoje = timezone.localdate()
        self.config = ConfiguracaoLocadora.obter()
        self.config.percentual_multa_atraso = Decimal('0')
        self.config.percentual_juros_diario = Decimal('0')
        self.config.dias_carencia = 0
        self.config.save()

    def contrato_ativo(self, **kwargs):
        agora = timezone.now()
        dados = dict(
            situacao='ativo', data_saida=agora - timedelta(days=14), km_saida=1000,
            combustivel_saida='cheio', data_devolucao_prevista=agora + timedelta(days=14),
        )
        dados.update(kwargs)
        return self.criar_contrato(**dados)

    def parcela(self, contrato, numero, dias, valor, **kwargs):
        return ParcelaContrato.objects.create(
            contrato=contrato, numero=numero, valor=Decimal(valor),
            data_vencimento=self.hoje + timedelta(days=dias), **kwargs,
        )

    def multa(self, contrato, valor, situacao='identificada'):
        from apps.financeiro.models import MultaTransito
        return MultaTransito.objects.create(
            veiculo=self.veiculo, contrato=contrato, data_infracao=self.hoje - timedelta(days=3),
            descricao='Avanço de sinal', valor=Decimal(valor), situacao=situacao,
        )


class DevolucaoVencidaTests(AbertosTestBase):
    """Contrato ativo com devolução vencida avisa o valor sem cobrança e o
    Prorrogar gera as parcelas desde a data prevista."""

    def test_contrato_vencido_mostra_aviso_com_valor_sem_cobranca(self):
        contrato = self.contrato_ativo(
            data_saida=timezone.now() - timedelta(days=30),
            data_devolucao_prevista=timezone.now() - timedelta(days=10),
        )

        resposta = self.client.get(reverse('contratos:detalhe', args=[contrato.pk]))

        self.assertTrue(contrato.devolucao_vencida)
        self.assertEqual(contrato.dias_devolucao_vencida, 10)
        self.assertEqual(contrato.valor_sem_cobranca_estimado, Decimal('1000.00'))
        self.assertEqual(contrato.semanas_para_regularizar, 2)
        self.assertContains(resposta, 'nenhuma parcela foi gerada')
        self.assertContains(resposta, 'Prorrogar e gerar as parcelas')

    def test_contrato_no_prazo_ou_vencendo_hoje_nao_avisa(self):
        no_prazo = self.contrato_ativo()
        hoje = self.contrato_ativo(data_devolucao_prevista=timezone.now() - timedelta(minutes=1))
        if timezone.localdate(hoje.data_devolucao_prevista) != self.hoje:
            self.skipTest('virada de dia durante o teste')

        self.assertFalse(no_prazo.devolucao_vencida)
        self.assertFalse(hoje.devolucao_vencida)

    def _prorrogar_vencido(self, **extra):
        prevista = timezone.now() - timedelta(days=10)
        contrato = self.contrato_ativo(
            data_saida=prevista - timedelta(days=14), data_devolucao_prevista=prevista,
        )
        nova = prevista + timedelta(days=14)
        self.client.post(reverse('contratos:prorrogar', args=[contrato.pk]), {
            'nova_data_devolucao': timezone.localtime(nova).strftime('%Y-%m-%dT%H:%M'), **extra,
        })
        return contrato, prevista

    def test_prorrogar_contrato_vencido_exige_escolha_sobre_encargos(self):
        contrato, prevista = self._prorrogar_vencido()

        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertFalse(contrato.parcelas.exists())
        self.assertEqual(contrato.data_devolucao_prevista, prevista)

    def test_prorrogar_vencido_com_encargos_mantem_vencimentos_originais(self):
        contrato, prevista = self._prorrogar_vencido(encargos_retroativos='sim')

        parcelas = list(contrato.parcelas.order_by('data_vencimento'))
        self.assertEqual(len(parcelas), 2)
        self.assertEqual(parcelas[0].data_vencimento, timezone.localdate(prevista))
        self.assertTrue(parcelas[0].em_atraso)
        self.assertFalse(Contrato.objects.get(pk=contrato.pk).devolucao_vencida)
        historico = contrato.historico.get(acao='prorrogacao')
        self.assertEqual(historico.dados['encargos_retroativos'], 'sim')

    def test_prorrogar_vencido_sem_encargos_faz_parcelas_vencidas_vencerem_hoje(self):
        self.config.percentual_multa_atraso = Decimal('2')
        self.config.percentual_juros_diario = Decimal('0.0333')
        self.config.save()
        contrato, prevista = self._prorrogar_vencido(encargos_retroativos='nao')

        parcelas = list(contrato.parcelas.order_by('numero'))
        self.assertEqual(len(parcelas), 2)
        self.assertEqual([p.data_vencimento for p in parcelas], [self.hoje, self.hoje])
        self.assertEqual([p.dias for p in parcelas], [7, 7])
        self.assertEqual(parcelas[0].valor, Decimal('700.00'))
        self.assertTrue(all(p.valor_corrigido == p.valor for p in parcelas))
        self.assertTrue(all(not p.em_atraso for p in parcelas))
        historico = contrato.historico.get(acao='prorrogacao')
        self.assertEqual(historico.dados['encargos_retroativos'], 'nao')
        self.assertIn('SEM multa', historico.motivo)

    def test_prorrogar_no_prazo_nao_pede_escolha(self):
        contrato = self.contrato_ativo()
        nova = contrato.data_devolucao_prevista + timedelta(days=7)
        self.client.post(reverse('contratos:prorrogar', args=[contrato.pk]), {
            'nova_data_devolucao': timezone.localtime(nova).strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(contrato.parcelas.count(), 1)
        self.assertIsNone(contrato.historico.get(acao='prorrogacao').dados['encargos_retroativos'])

    def test_simulacao_mostra_encargos_no_modal(self):
        self.config.percentual_multa_atraso = Decimal('2')
        self.config.save()
        prevista = timezone.now() - timedelta(days=10)
        contrato = self.contrato_ativo(
            data_saida=prevista - timedelta(days=14), data_devolucao_prevista=prevista,
        )

        simulacao = contrato.simular_prorrogacao_retroativa()
        self.assertEqual(simulacao['quantidade'], 2)
        self.assertEqual(simulacao['valor'], Decimal('1400.00'))
        self.assertEqual(simulacao['encargos'], Decimal('28.00'))
        resposta = self.client.get(reverse('contratos:detalhe', args=[contrato.pk]))
        self.assertContains(resposta, 'encargos_retroativos')
        self.assertContains(resposta, 'A devolução prevista já venceu')


class MultaCobradaNoContratoTests(AbertosTestBase):
    """Multa marcada como "Cobrada ao cliente" vira cobrança no contrato,
    sem multa nem juros de atraso."""

    def test_multa_cobrada_em_contrato_encerrado_volta_para_contas_a_receber(self):
        contrato = self.contrato_ativo(
            data_saida=timezone.now() - timedelta(days=34),
            data_devolucao_prevista=timezone.now() - timedelta(days=20),
        )
        self.parcela(contrato, 1, -14, '1400.00', situacao='pago', data_pagamento=timezone.now())
        PagamentoContrato.objects.create(
            contrato=contrato, parcela=contrato.parcelas.get(), tipo='locacao',
            forma_pagamento='pix', valor=Decimal('1400.00'),
        )
        contrato.situacao = 'encerrado'
        contrato.data_devolucao_real = timezone.now() - timedelta(days=20)
        contrato.save()
        conta = Contrato.objects.get(pk=contrato.pk).conta_receber
        self.assertEqual(conta.situacao, 'pago')

        multa = self.multa(contrato, '195.23', situacao='cobrada_cliente')

        conta.refresh_from_db()
        parcela = multa.parcela
        self.assertEqual((parcela.tipo, parcela.valor), ('multa', Decimal('195.23')))
        self.assertEqual(parcela.data_vencimento, self.hoje + timedelta(days=7))
        self.assertEqual(conta.valor_total, Decimal('1595.23'))
        self.assertEqual(conta.situacao, 'pago_parcial')
        self.assertFalse(ContaReceber.objects.com_parcelas_atrasadas(self.hoje).get(pk=conta.pk).tem_atraso)
        self.assertEqual(conta.data_vencimento, parcela.data_vencimento)

    def test_multa_atrasada_nao_tem_encargos_e_paga_sem_contar_em_dobro(self):
        self.config.percentual_multa_atraso = Decimal('2')
        self.config.percentual_juros_diario = Decimal('1')
        self.config.save()
        contrato = self.contrato_ativo()
        multa = self.multa(contrato, '200.00', situacao='cobrada_cliente')
        ParcelaContrato.objects.filter(pk=multa.parcela_id).update(data_vencimento=self.hoje - timedelta(days=5))
        parcela = ParcelaContrato.objects.get(pk=multa.parcela_id)
        self.assertEqual(parcela.valor_corrigido, Decimal('200.00'))

        self.client.post(reverse('contratos:parcela-pagar', args=[contrato.pk, parcela.pk]), {
            'forma_pagamento': 'pix', 'valor': '200.00',
        })

        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(PagamentoContrato.objects.get(parcela=parcela).tipo, 'multa')
        self.assertEqual(contrato.total_geral, Decimal('200.00'))
        self.assertEqual(contrato.saldo_devedor, Decimal('0.00'))

    def test_multa_contestada_cancela_a_cobranca_e_ao_voltar_reabre(self):
        contrato = self.contrato_ativo()
        multa = self.multa(contrato, '130.00', situacao='cobrada_cliente')

        multa.situacao = 'contestada'
        multa.save()
        self.assertEqual(ParcelaContrato.objects.get(pk=multa.parcela_id).situacao, 'cancelada')

        multa.situacao = 'cobrada_cliente'
        multa.valor = Decimal('88.38')
        multa.save()
        parcela = ParcelaContrato.objects.get(pk=multa.parcela_id)
        self.assertEqual((parcela.situacao, parcela.valor), ('pendente', Decimal('88.38')))
        self.assertEqual(contrato.parcelas.filter(tipo='multa').count(), 1)

    def test_multa_futura_nao_bloqueia_o_check_in(self):
        contrato = self.contrato_ativo()
        self.multa(contrato, '130.00', situacao='cobrada_cliente')

        self.assertFalse(contrato.parcelas_devidas_ate(self.hoje).exists())

    def test_caucao_menor_que_a_multa_deixa_o_restante_em_cobranca(self):
        contrato = self.contrato_ativo(
            situacao='aguardando_devolucao', data_devolucao_real=timezone.now(),
            caucao_valor=Decimal('100.00'), caucao_situacao='pago',
        )
        PagamentoContrato.objects.create(
            contrato=contrato, tipo='caucao', forma_pagamento='pix', valor=Decimal('100.00'),
        )
        multa = self.multa(contrato, '250.00')

        self.client.post(reverse('contratos:encerrar', args=[contrato.pk]))

        multa.refresh_from_db()
        self.assertEqual(multa.situacao, 'cobrada_cliente')
        restante = contrato.parcelas.get(tipo='multa', situacao='pendente')
        self.assertEqual(restante.valor, Decimal('150.00'))
        self.assertEqual(Contrato.objects.get(pk=contrato.pk).saldo_devedor, Decimal('150.00'))


class RecebimentoEmOrdemTests(AbertosTestBase):
    """Recebimento em Contas a Receber quita com juros, pula o caução,
    liga o pagamento à parcela e divide a parcela no pagamento parcial."""

    def receber(self, contrato, valor):
        return self.client.post(reverse('financeiro:receber', args=[contrato.conta_receber.pk]), {
            'forma_pagamento': 'pix', 'tipo': 'locacao', 'valor': valor,
            'data_pagamento': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        })

    def test_pagamento_com_juros_quita_a_parcela_sem_credito_falso(self):
        self.config.percentual_multa_atraso = Decimal('5')
        self.config.save()
        contrato = self.contrato_ativo()
        parcela = self.parcela(contrato, 1, -3, '700.00')

        self.receber(contrato, '735.00')

        parcela.refresh_from_db()
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(parcela.pagamento_vinculado.valor, Decimal('735.00'))
        self.assertEqual(contrato.saldo_devedor, Decimal('0.00'))

    def test_pagamento_parcial_divide_a_parcela(self):
        contrato = self.contrato_ativo()
        parcela = self.parcela(contrato, 1, -3, '700.00')

        self.receber(contrato, '500.00')

        parcela.refresh_from_db()
        restante = contrato.parcelas.get(situacao='pendente')
        self.assertEqual((parcela.valor, parcela.situacao), (Decimal('500.00'), 'pago'))
        self.assertEqual((restante.valor, restante.data_vencimento), (Decimal('200.00'), parcela.data_vencimento))
        self.assertEqual(Contrato.objects.get(pk=contrato.pk).saldo_devedor, Decimal('200.00'))

    def test_parcela_de_caucao_nao_e_quitada_como_aluguel(self):
        contrato = self.contrato_ativo()
        caucao = self.parcela(contrato, 1, -14, '500.00', tipo='caucao')
        aluguel = self.parcela(contrato, 2, -7, '700.00')

        self.receber(contrato, '700.00')

        caucao.refresh_from_db()
        aluguel.refresh_from_db()
        self.assertEqual(caucao.situacao, 'pendente')
        self.assertEqual(aluguel.situacao, 'pago')

    def test_valor_acima_das_parcelas_fica_como_credito_real(self):
        contrato = self.contrato_ativo()
        self.parcela(contrato, 1, -3, '700.00')

        self.receber(contrato, '800.00')

        self.assertEqual(Contrato.objects.get(pk=contrato.pk).saldo_devedor, Decimal('-100.00'))

    def test_tela_do_contrato_usa_a_mesma_regra(self):
        contrato = self.contrato_ativo()
        parcela = self.parcela(contrato, 1, -3, '700.00')

        self.client.post(reverse('contratos:pagamentos', args=[contrato.pk]), {
            'forma_pagamento': 'pix', 'tipo': 'locacao', 'valor': '300.00',
            'data_pagamento': timezone.localtime().strftime('%Y-%m-%dT%H:%M'), 'observacoes': '',
        })

        parcela.refresh_from_db()
        self.assertEqual((parcela.valor, parcela.situacao), (Decimal('300.00'), 'pago'))
        self.assertEqual(contrato.parcelas.get(situacao='pendente').valor, Decimal('400.00'))


class RelatorioClientesTests(AbertosTestBase):
    """Relatório de clientes soma a receita pela data do pagamento, inclusive
    de contratos ativos, e sem o caução."""

    def test_contrato_ativo_com_pagamento_no_periodo_entra_sem_caucao(self):
        contrato = self.contrato_ativo(data_saida=timezone.now())
        PagamentoContrato.objects.create(
            contrato=contrato, tipo='locacao', forma_pagamento='pix', valor=Decimal('700.00'),
        )
        PagamentoContrato.objects.create(
            contrato=contrato, tipo='caucao', forma_pagamento='pix', valor=Decimal('500.00'),
        )

        contexto = self.client.get(
            reverse('core:relatorio-clientes'), {'ano': self.hoje.year, 'mes': self.hoje.month},
        ).context

        linha = contexto['clientes_receita'][0]
        self.assertEqual((linha['receita'], linha['locacoes']), (Decimal('700.00'), 1))
        self.assertEqual(contexto['total_ativos'], 1)
        self.assertEqual(contexto['novos'], 1)


class MultaBotoesDeSituacaoTests(AbertosTestBase):
    """Botões de situação na tela da multa seguem as transições permitidas."""

    def mudar(self, multa, situacao):
        return self.client.post(reverse('financeiro:multa-situacao', args=[multa.pk]), {'situacao': situacao})

    def test_tela_mostra_os_botoes_da_situacao_atual(self):
        multa = self.multa(self.contrato_ativo(), '130.00')

        resposta = self.client.get(reverse('financeiro:multa-detalhe', args=[multa.pk]))

        self.assertContains(resposta, 'Cobrar do cliente')
        self.assertContains(resposta, 'Marcar como paga')
        self.assertContains(resposta, 'Contestar')

    def test_cobrar_do_cliente_cria_a_cobranca_no_contrato(self):
        contrato = self.contrato_ativo()
        multa = self.multa(contrato, '130.00')

        self.mudar(multa, 'cobrada_cliente')

        multa.refresh_from_db()
        self.assertEqual(multa.situacao, 'cobrada_cliente')
        self.assertEqual(multa.parcela.contrato, contrato)
        resposta = self.client.get(reverse('financeiro:multa-detalhe', args=[multa.pk]))
        self.assertContains(resposta, 'Cobrança no Contrato')

    def test_nao_cobra_multa_sem_contrato(self):
        from apps.financeiro.models import MultaTransito
        multa = MultaTransito.objects.create(
            veiculo=self.veiculo, data_infracao=self.hoje - timedelta(days=400),
            descricao='Sem contrato', valor=Decimal('130.00'), situacao='identificada',
        )

        self.mudar(multa, 'cobrada_cliente')

        multa.refresh_from_db()
        self.assertEqual(multa.situacao, 'identificada')
        self.assertIsNone(multa.parcela)

    def test_identificar_exige_o_nome_do_condutor(self):
        multa = self.multa(self.contrato_ativo(), '130.00', situacao='pendente_identificacao')

        self.mudar(multa, 'identificada')
        multa.refresh_from_db()
        self.assertEqual(multa.situacao, 'pendente_identificacao')

        multa.condutor_nome = 'Fulano'
        multa.save()
        self.mudar(multa, 'identificada')
        multa.refresh_from_db()
        self.assertEqual(multa.situacao, 'identificada')

    def test_multa_paga_nao_muda_mais(self):
        multa = self.multa(self.contrato_ativo(), '130.00', situacao='paga')

        self.mudar(multa, 'contestada')

        multa.refresh_from_db()
        self.assertEqual(multa.situacao, 'paga')


class VeiculoMudarSituacaoTests(AbertosTestBase):
    """Botão de situação do veículo só permite o que não conflita com contrato, reserva ou OS."""

    def mudar(self, situacao):
        self.client.post(reverse('frota:situacao', args=[self.veiculo.pk]), {'situacao': situacao})
        self.veiculo.refresh_from_db()

    def test_disponivel_vai_para_manutencao_e_volta(self):
        self.mudar('manutencao')
        self.assertEqual(self.veiculo.situacao, 'manutencao')
        self.mudar('disponivel')
        self.assertEqual(self.veiculo.situacao, 'disponivel')
        self.mudar('inativo')
        self.assertEqual(self.veiculo.situacao, 'inativo')

    def test_veiculo_locado_nao_muda_e_a_tela_explica(self):
        contrato = self.contrato_ativo()
        Veiculo.objects.filter(pk=self.veiculo.pk).update(situacao='em_uso')

        self.mudar('disponivel')

        self.assertEqual(self.veiculo.situacao, 'em_uso')
        resposta = self.client.get(reverse('frota:detalhe', args=[self.veiculo.pk]))
        self.assertContains(resposta, f'está locado no contrato {contrato.numero}')
        self.assertNotContains(resposta, 'Mudar situação')

    def test_nao_libera_veiculo_com_os_em_andamento(self):
        from apps.manutencao.models import OrdemManutencao
        OrdemManutencao.objects.create(
            veiculo=self.veiculo, tipo='corretiva', descricao='Freio', situacao='em_andamento',
        )
        Veiculo.objects.filter(pk=self.veiculo.pk).update(situacao='manutencao')

        self.mudar('disponivel')

        self.assertEqual(self.veiculo.situacao, 'manutencao')

    def test_nao_inativa_com_contrato_aberto(self):
        self.criar_contrato()

        self.mudar('inativo')

        self.assertEqual(self.veiculo.situacao, 'disponivel')

    def test_nao_permite_marcar_em_uso_manualmente(self):
        self.mudar('em_uso')

        self.assertEqual(self.veiculo.situacao, 'disponivel')


class SituacaoSoPelosBotoesTests(AbertosTestBase):
    """Editar veículo ou multa não muda a situação; isso fica só nos botões."""

    def test_editar_veiculo_locado_nao_libera_o_carro(self):
        from apps.fleet.forms import VeiculoForm
        Veiculo.objects.filter(pk=self.veiculo.pk).update(situacao='em_uso')
        self.veiculo.refresh_from_db()
        dados = {campo: valor for campo, valor in VeiculoForm(instance=self.veiculo).initial.items() if valor is not None}
        dados.update({'situacao': 'disponivel', 'grupo': self.grupo.pk})

        form = VeiculoForm(dados, instance=self.veiculo)

        self.assertNotIn('situacao', form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.veiculo.refresh_from_db()
        self.assertEqual(self.veiculo.situacao, 'em_uso')

    def test_cadastro_de_veiculo_so_oferece_situacoes_manuais(self):
        from apps.fleet.forms import VeiculoForm
        opcoes = dict(VeiculoForm().fields['situacao'].choices)
        self.assertEqual(set(opcoes), {'disponivel', 'manutencao', 'inativo'})

    def test_editar_multa_nao_muda_a_situacao(self):
        from apps.financeiro.forms import MultaTransitoForm
        multa = self.multa(self.contrato_ativo(), '130.00')
        self.assertNotIn('situacao', MultaTransitoForm(instance=multa).fields)
        self.assertNotIn('cobrada_cliente', dict(MultaTransitoForm().fields['situacao'].choices))
        resposta = self.client.get(reverse('financeiro:multa-editar', args=[multa.pk]))
        self.assertContains(resposta, 'use os botões na tela da multa')


def _dt_local(dt):
    return timezone.localtime(dt).strftime('%Y-%m-%dT%H:%M')


class CorrecoesFluxosRecebimentoTests(AbertosTestBase):

    def recarregar(self, contrato):
        contrato = Contrato.objects.get(pk=contrato.pk)
        contrato.limpar_cache_financeiro()
        return contrato

    def receber_conta(self, contrato, tipo, valor):
        from apps.financeiro.models import ContaReceber
        conta = ContaReceber.objects.get(contrato=contrato)
        return self.client.post(reverse('financeiro:receber', args=[conta.pk]), {
            'forma_pagamento': 'pix', 'tipo': tipo, 'valor': valor,
            'data_pagamento': _dt_local(timezone.now()),
        })

    def multa_cobrada(self, contrato, valor='195.23'):
        multa = self.multa(contrato, valor)
        multa.situacao = 'cobrada_cliente'
        multa.save()
        return multa

    def test_recebimento_de_multa_quita_a_parcela_da_multa(self):
        contrato = self.contrato_ativo()
        multa = self.multa_cobrada(contrato)

        self.receber_conta(contrato, 'multa', '195.23')

        multa.refresh_from_db()
        contrato = self.recarregar(contrato)
        self.assertEqual(multa.parcela.situacao, 'pago')
        self.assertEqual(multa.parcela.pagamento_vinculado.valor, Decimal('195.23'))
        self.assertEqual(contrato.saldo_devedor, Decimal('0.00'))

    def test_recebimento_de_avaria_nao_e_aceito_fora_da_tela_de_avarias(self):
        from apps.contracts.models import AvariaContrato
        contrato = self.contrato_ativo()
        AvariaContrato.objects.create(contrato=contrato, descricao='Para-choque', valor_cobrado=Decimal('300'), situacao='cobrada')

        self.receber_conta(contrato, 'avaria', '300.00')
        self.client.post(reverse('contratos:pagamentos', args=[contrato.pk]), {
            'forma_pagamento': 'pix', 'tipo': 'avaria', 'valor': '300.00',
            'data_pagamento': _dt_local(timezone.now()),
        })

        self.assertFalse(PagamentoContrato.objects.filter(contrato=contrato).exists())

    def test_caucao_recebido_em_contas_a_receber_quita_a_parcela_e_fica_em_poder(self):
        contrato = self.contrato_ativo(caucao_valor=Decimal('500'))
        parcela = self.parcela(contrato, 1, -14, '500.00', tipo='caucao')

        self.receber_conta(contrato, 'caucao', '500.00')

        parcela.refresh_from_db()
        contrato = self.recarregar(contrato)
        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(contrato.caucao_situacao, 'pago')
        self.assertEqual(contrato.caucao_em_poder, Decimal('500.00'))

    def test_caucao_pelo_detalhe_do_contrato_tira_a_parcela_da_agenda(self):
        contrato = self.contrato_ativo(caucao_valor=Decimal('500'))
        parcela = self.parcela(contrato, 1, -14, '500.00', tipo='caucao')

        self.client.post(reverse('contratos:pagamentos', args=[contrato.pk]), {
            'forma_pagamento': 'pix', 'tipo': 'caucao', 'valor': '500.00',
            'data_pagamento': _dt_local(timezone.now()),
        })

        parcela.refresh_from_db()
        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(self.recarregar(contrato).caucao_situacao, 'pago')

    def test_caucao_parcial_continua_pendente_mas_abate_no_encerramento(self):
        contrato = self.contrato_ativo(caucao_valor=Decimal('500'))
        self.parcela(contrato, 1, -14, '500.00', tipo='caucao')
        self.receber_conta(contrato, 'caucao', '200.00')
        contrato = self.recarregar(contrato)
        self.assertEqual(contrato.caucao_situacao, 'pendente')
        self.assertEqual(contrato.caucao_em_poder, Decimal('200.00'))

        divida = self.parcela(contrato, 2, -2, '150.00')
        contrato.situacao = 'aguardando_devolucao'
        contrato.data_devolucao_real = timezone.now()
        contrato.save()
        self.client.post(reverse('contratos:encerrar', args=[contrato.pk]))

        divida.refresh_from_db()
        contrato = self.recarregar(contrato)
        self.assertEqual(divida.situacao, 'pago')
        self.assertEqual(contrato.caucao_situacao, 'devolvido_parcial')
        self.assertTrue(DespesaOperacional.objects.filter(categoria='caucao', valor=Decimal('50.00')).exists())

    def test_tipo_de_recebimento_desconhecido_e_recusado(self):
        contrato = self.contrato_ativo()
        with self.assertRaises(ValueError):
            contrato.registrar_recebimento('avaria', Decimal('10'), 'pix', timezone.now())

    def test_pagamento_com_data_futura_e_recusado(self):
        contrato = self.contrato_ativo()
        self.parcela(contrato, 1, 0, '700.00')
        from apps.financeiro.models import ContaReceber
        conta = ContaReceber.objects.get(contrato=contrato)
        self.client.post(reverse('financeiro:receber', args=[conta.pk]), {
            'forma_pagamento': 'pix', 'tipo': 'locacao', 'valor': '700.00',
            'data_pagamento': _dt_local(timezone.now() + timedelta(days=3)),
        })
        self.assertFalse(PagamentoContrato.objects.filter(contrato=contrato).exists())

    def test_conta_a_receber_nao_pode_mais_ser_cancelada_manualmente(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse('financeiro:conta-receber-cancelar', args=[1])


class CorrecoesFluxosContratoTests(AbertosTestBase):

    def recarregar(self, contrato):
        contrato = Contrato.objects.get(pk=contrato.pk)
        contrato.limpar_cache_financeiro()
        return contrato

    def quebrar(self, contrato):
        return self.client.post(reverse('contratos:encerramento-antecipado', args=[contrato.pk]), {
            'data_devolucao_real': _dt_local(timezone.now() - timedelta(minutes=5)), 'km_devolucao': 1500,
            'combustivel_devolucao': 'cheio', 'motivo_encerramento': 'cliente_solicitou',
        })

    def test_quebra_de_contrato_mantem_cobranca_da_multa(self):
        contrato = self.contrato_ativo()
        multa = self.multa(contrato, '195.23')
        multa.situacao = 'cobrada_cliente'
        multa.save()
        futura = self.parcela(contrato, 1, 5, '700.00', tipo='semanal')

        self.quebrar(contrato)

        multa.refresh_from_db()
        futura.refresh_from_db()
        contrato = self.recarregar(contrato)
        self.assertEqual(contrato.situacao, 'encerrado')
        self.assertEqual(futura.situacao, 'cancelada')
        self.assertEqual(multa.parcela.situacao, 'pendente')
        self.assertEqual(contrato.saldo_devedor, Decimal('195.23'))

    def test_quebra_de_contrato_abate_multa_do_caucao(self):
        contrato = self.contrato_ativo(caucao_valor=Decimal('500'), caucao_situacao='pago')
        PagamentoContrato.objects.create(contrato=contrato, tipo='caucao', forma_pagamento='pix', valor=Decimal('500'))
        multa = self.multa(contrato, '195.23')
        multa.situacao = 'cobrada_cliente'
        multa.save()

        self.quebrar(contrato)

        multa.refresh_from_db()
        self.assertEqual(multa.parcela.situacao, 'pago')
        self.assertEqual(self.recarregar(contrato).caucao_situacao, 'devolvido_parcial')

    def test_checkin_com_divida_e_caucao_abate_no_encerramento(self):
        contrato = self.contrato_ativo(caucao_valor=Decimal('500'), caucao_situacao='pago')
        PagamentoContrato.objects.create(contrato=contrato, tipo='caucao', forma_pagamento='pix', valor=Decimal('500'))
        divida = self.parcela(contrato, 1, -2, '300.00')

        self.client.post(reverse('contratos:checkin', args=[contrato.pk]), {
            'data_devolucao_real': _dt_local(timezone.now()), 'km_devolucao': 1500,
            'combustivel_devolucao': 'cheio',
        })
        self.assertEqual(self.recarregar(contrato).situacao, 'aguardando_devolucao')
        self.client.post(reverse('contratos:encerrar', args=[contrato.pk]))

        divida.refresh_from_db()
        contrato = self.recarregar(contrato)
        self.assertEqual(divida.situacao, 'pago')
        self.assertEqual(contrato.saldo_devedor, Decimal('0.00'))
        self.assertTrue(DespesaOperacional.objects.filter(categoria='caucao', valor=Decimal('200.00')).exists())

    def test_contrato_ativo_nao_pode_ser_cancelado(self):
        contrato = self.contrato_ativo()
        vencida = self.parcela(contrato, 1, -7, '700.00')

        self.client.post(reverse('contratos:cancelar', args=[contrato.pk]))

        vencida.refresh_from_db()
        self.assertEqual(self.recarregar(contrato).situacao, 'ativo')
        self.assertEqual(vencida.situacao, 'pendente')

    def test_contrato_aberto_com_pagamento_nao_pode_ser_cancelado(self):
        contrato = self.criar_contrato(caucao_valor=Decimal('500'))
        PagamentoContrato.objects.create(contrato=contrato, tipo='caucao', forma_pagamento='pix', valor=Decimal('500'))

        self.client.post(reverse('contratos:cancelar', args=[contrato.pk]))

        self.assertEqual(self.recarregar(contrato).situacao, 'aberto')

    def test_contrato_aberto_sem_pagamento_e_cancelado_com_historico(self):
        contrato = self.criar_contrato()
        self.client.post(reverse('contratos:cancelar', args=[contrato.pk]))
        self.assertEqual(self.recarregar(contrato).situacao, 'cancelado')
        self.assertTrue(contrato.historico.filter(acao='cancelamento').exists())

    def test_checkout_cobra_so_a_diferenca_do_caucao_ja_adiantado(self):
        CNHCliente.objects.create(
            cliente=self.cliente, numero='12345678900', categoria='B',
            validade=self.hoje + timedelta(days=365), principal=True,
        )
        contrato = self.criar_contrato(caucao_valor=Decimal('500'))
        self.client.post(reverse('contratos:pagamentos', args=[contrato.pk]), {
            'forma_pagamento': 'pix', 'tipo': 'caucao', 'valor': '200.00',
            'data_pagamento': _dt_local(timezone.now()),
        })

        self.client.post(reverse('contratos:checkout', args=[contrato.pk]), {
            'km_saida': 1000, 'combustivel_saida': 'cheio', 'caucao_situacao': 'pago',
            'caucao_forma_pagamento': 'dinheiro',
        })

        contrato = self.recarregar(contrato)
        self.assertEqual(contrato.situacao, 'ativo')
        self.assertEqual(contrato.total_caucao_coletado, Decimal('500.00'))
        parcela = contrato.parcelas.get(tipo='caucao')
        self.assertEqual(parcela.valor, Decimal('300.00'))
        self.assertEqual(parcela.pagamento_vinculado.valor, Decimal('300.00'))

    def test_agenda_lista_avaria_cobrada_de_contrato_encerrado(self):
        from apps.contracts.models import AvariaContrato
        contrato = self.contrato_ativo(situacao='encerrado', data_devolucao_real=timezone.now())
        AvariaContrato.objects.create(contrato=contrato, descricao='Risco porta', valor_cobrado=Decimal('250'), situacao='cobrada')

        resposta = self.client.get(reverse('financeiro:agenda'))

        self.assertContains(resposta, 'Risco porta')
        self.assertContains(resposta, 'Avarias a cobrar')


class EstornoPagamentoTests(AbertosTestBase):

    def estornar(self, pagamento, motivo='lançado errado'):
        return self.client.post(
            reverse('contratos:pagamento-estornar', args=[pagamento.contrato_id, pagamento.pk]),
            {'motivo': motivo},
        )

    def test_estorno_reabre_a_parcela_e_registra_historico(self):
        contrato = self.contrato_ativo()
        parcela = self.parcela(contrato, 1, -1, '700.00')
        pagamento = parcela.quitar(Decimal('700.00'), 'pix', timezone.now(), self.user)

        self.estornar(pagamento)

        parcela.refresh_from_db()
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(parcela.situacao, 'pendente')
        self.assertFalse(PagamentoContrato.objects.filter(pk=pagamento.pk).exists())
        self.assertEqual(contrato.saldo_devedor, Decimal('700.00'))
        historico = contrato.historico.get(acao='estorno_pagamento')
        self.assertEqual(historico.dados['valor'], '700.00')
        self.assertEqual(historico.motivo, 'lançado errado')

    def test_estorno_exige_motivo(self):
        contrato = self.contrato_ativo()
        parcela = self.parcela(contrato, 1, -1, '700.00')
        pagamento = parcela.quitar(Decimal('700.00'), 'pix', timezone.now(), self.user)

        self.estornar(pagamento, motivo='')

        self.assertTrue(PagamentoContrato.objects.filter(pk=pagamento.pk).exists())

    def test_estorno_de_avaria_volta_para_cobrada(self):
        from apps.contracts.models import AvariaContrato
        contrato = self.contrato_ativo()
        avaria = AvariaContrato.objects.create(contrato=contrato, descricao='X', valor_cobrado=Decimal('300'), situacao='cobrada')
        self.client.post(reverse('contratos:avaria-pagar', args=[contrato.pk, avaria.pk]), {'forma_pagamento': 'pix'})
        pagamento = PagamentoContrato.objects.get(avaria=avaria)

        self.estornar(pagamento)

        avaria.refresh_from_db()
        self.assertEqual(avaria.situacao, 'cobrada')

    def test_estorno_do_caucao_volta_situacao_para_pendente(self):
        contrato = self.contrato_ativo(caucao_valor=Decimal('500'))
        parcela = self.parcela(contrato, 1, -14, '500.00', tipo='caucao')
        contrato.registrar_recebimento('caucao', Decimal('500'), 'pix', timezone.now(), self.user)
        pagamento = PagamentoContrato.objects.get(contrato=contrato, tipo='caucao')

        self.estornar(pagamento)

        parcela.refresh_from_db()
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(parcela.situacao, 'pendente')
        self.assertEqual(contrato.caucao_situacao, 'pendente')

    def test_abatimento_do_caucao_nao_pode_ser_estornado(self):
        from .models import FORMA_ABATIDO_CAUCAO
        contrato = self.contrato_ativo()
        parcela = self.parcela(contrato, 1, -1, '700.00')
        pagamento = parcela.quitar(Decimal('700.00'), FORMA_ABATIDO_CAUCAO, timezone.now(), self.user)

        self.estornar(pagamento)

        self.assertTrue(PagamentoContrato.objects.filter(pk=pagamento.pk).exists())

    def test_estorno_restrito_ao_admin(self):
        from django.contrib.auth.models import Group
        contrato = self.contrato_ativo()
        parcela = self.parcela(contrato, 1, -1, '700.00')
        pagamento = parcela.quitar(Decimal('700.00'), 'pix', timezone.now(), self.user)
        atendente = User.objects.create_user('atendente_x', password='x')
        atendente.groups.add(Group.objects.get_or_create(name='atendente')[0])
        self.client.force_login(atendente)

        self.estornar(pagamento)

        self.assertTrue(PagamentoContrato.objects.filter(pk=pagamento.pk).exists())


class ConferirIntegridadeFinanceiraTests(AbertosTestBase):

    def conferir(self):
        from io import StringIO
        from django.core.management import call_command
        saida = StringIO()
        call_command('conferir_integridade_financeira', stdout=saida)
        return saida.getvalue()

    def test_aponta_registros_gravados_pelos_fluxos_antigos(self):
        contrato = self.contrato_ativo(caucao_valor=Decimal('500'))
        PagamentoContrato.objects.create(contrato=contrato, tipo='caucao', forma_pagamento='pix', valor=Decimal('500'))
        parcela = self.parcela(contrato, 1, -3, '700.00')
        PagamentoContrato.objects.create(
            contrato=contrato, parcela=parcela, tipo='locacao', forma_pagamento='pix', valor=Decimal('200'),
        )
        ParcelaContrato.objects.filter(pk=parcela.pk).update(situacao='pago')

        saida = self.conferir()

        self.assertIn(f'{contrato.numero} (Ativo): caução 500', saida)
        self.assertIn('quitada com 200', saida)
        self.assertIn('2 registro(s) para conferir', saida)
        self.assertEqual(PagamentoContrato.objects.filter(contrato=contrato).count(), 2)

    def test_sem_incoerencia(self):
        self.assertIn('Nenhuma incoerência encontrada', self.conferir())


class ToleranciaDevolucaoTests(AbertosTestBase):

    def checkin(self, contrato, atraso):
        prazo = contrato.data_saida + timedelta(days=contrato.dias_contratados_no_fechamento())
        return self.client.post(reverse('contratos:checkin', args=[contrato.pk]), {
            'data_devolucao_real': timezone.localtime(prazo + atraso).strftime('%Y-%m-%dT%H:%M'),
            'km_devolucao': 1200, 'combustivel_devolucao': 'cheio',
        }, follow=True)

    def contrato_de_7_dias(self):
        saida = (timezone.now() - timedelta(days=8)).replace(second=0, microsecond=0)
        return self.contrato_ativo(data_saida=saida, data_devolucao_prevista=saida + timedelta(days=7))

    def test_atraso_dentro_da_tolerancia_nao_cobra_e_avisa(self):
        contrato = self.contrato_de_7_dias()

        resposta = self.checkin(contrato, timedelta(minutes=50))

        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(contrato.dias_extras, 0)
        self.assertFalse(contrato.parcelas.filter(tipo='acerto').exists())
        self.assertContains(resposta, 'dentro da tolerância de 60 min')

    def test_atraso_acima_da_tolerancia_cobra_diaria_e_avisa(self):
        contrato = self.contrato_de_7_dias()

        resposta = self.checkin(contrato, timedelta(minutes=61))

        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(contrato.dias_extras, 1)
        self.assertEqual(contrato.parcelas.get(tipo='acerto').valor, Decimal('100.00'))
        self.assertContains(resposta, 'passou da tolerância de 60 min')

    def test_tolerancia_zero_volta_a_cobrar_qualquer_fracao(self):
        self.config.tolerancia_devolucao_minutos = 0
        self.config.save()
        contrato = self.contrato_de_7_dias()

        self.checkin(contrato, timedelta(minutes=1))

        self.assertEqual(Contrato.objects.get(pk=contrato.pk).dias_extras, 1)

    def test_tela_de_checkin_informa_tolerancia_e_limite(self):
        contrato = self.contrato_de_7_dias()
        limite = timezone.localtime(contrato.limite_devolucao_sem_extra())

        resposta = self.client.get(reverse('contratos:checkin', args=[contrato.pk]))

        self.assertContains(resposta, 'Tolerância de devolução: 60 min.')
        self.assertContains(resposta, limite.strftime('%d/%m/%Y %H:%M'))
        self.assertContains(resposta, 'regra-devolucao')

    def test_dias_por_ciclo(self):
        inicio = timezone.now()
        uma_hora = timedelta(hours=1)
        self.assertEqual(Contrato.dias_por_ciclo(inicio, inicio + timedelta(days=3, minutes=60), uma_hora), 3)
        self.assertEqual(Contrato.dias_por_ciclo(inicio, inicio + timedelta(days=3, minutes=61), uma_hora), 4)
        self.assertEqual(Contrato.dias_por_ciclo(inicio, inicio + timedelta(minutes=30), uma_hora), 1)


class ProrrogacaoSemEncargosMistaTests(AbertosTestBase):

    def test_so_parcelas_vencidas_mudam_de_data(self):
        prevista = timezone.now() - timedelta(days=3)
        contrato = self.contrato_ativo(data_saida=prevista - timedelta(days=7), data_devolucao_prevista=prevista)
        nova = prevista + timedelta(days=14)
        self.client.post(reverse('contratos:prorrogar', args=[contrato.pk]), {
            'nova_data_devolucao': timezone.localtime(nova).strftime('%Y-%m-%dT%H:%M'), 'encargos_retroativos': 'nao',
        })
        datas = list(contrato.parcelas.order_by('numero').values_list('data_vencimento', flat=True))
        self.assertEqual(datas, [self.hoje, timezone.localdate(prevista) + timedelta(days=7)])


class CobrancaPorPacoteTests(AbertosTestBase):

    def contrato_pacote(self, forma='semanal', semanal='750.00', mensal='2800.00', diaria='120.00', **extra):
        return self.contrato_ativo(
            diaria=Decimal(diaria), forma_cobranca=forma,
            valor_semanal=Decimal(semanal) if semanal else None,
            valor_mensal=Decimal(mensal) if mensal else None,
            **extra,
        )

    def gerar(self, contrato, inicio, dias, forma=None):
        contrato.parcelas.all().delete()
        gerar_parcelas(contrato, inicio, inicio + timedelta(days=dias), tipo_cobranca=forma or contrato.forma_cobranca)
        return list(contrato.parcelas.order_by('numero').values_list('valor', 'dias', 'periodo_completo'))

    def test_semana_fechada_paga_pacote_e_dias_soltos_pela_diaria(self):
        contrato = self.contrato_pacote()
        self.assertEqual(self.gerar(contrato, date(2026, 1, 1), 10), [
            (Decimal('750.00'), 7, True), (Decimal('360.00'), 3, False),
        ])

    def test_dias_soltos_limitados_ao_pacote(self):
        contrato = self.contrato_pacote(semanal='600.00', mensal=None)
        self.assertEqual(self.gerar(contrato, date(2026, 1, 1), 13), [
            (Decimal('600.00'), 7, True), (Decimal('600.00'), 6, False),
        ])

    def test_mes_fechado_paga_pacote_mesmo_com_31_dias(self):
        contrato = self.contrato_pacote(forma='mensal')
        self.assertEqual(self.gerar(contrato, date(2026, 1, 1), 45), [
            (Decimal('2800.00'), 31, True), (Decimal('1680.00'), 14, False),
        ])

    def test_contrato_curto_paga_diaria(self):
        contrato = self.contrato_pacote()
        self.assertEqual(self.gerar(contrato, date(2026, 1, 1), 3), [(Decimal('360.00'), 3, False)])

    def test_adicionais_somam_por_dia_ao_pacote(self):
        from .models import AdicionalContrato
        contrato = self.contrato_pacote()
        AdicionalContrato.objects.create(contrato=contrato, tipo='gps', diaria=Decimal('10.00'), quantidade=1)
        contrato.limpar_cache_financeiro()
        self.assertEqual(self.gerar(contrato, date(2026, 1, 1), 10), [
            (Decimal('820.00'), 7, True), (Decimal('390.00'), 3, False),
        ])

    def test_incluir_adicional_reprecifica_mantendo_o_pacote(self):
        contrato = self.contrato_pacote()
        hoje = self.hoje
        contrato.parcelas.all().delete()
        gerar_parcelas(contrato, hoje + timedelta(days=1), hoje + timedelta(days=11))
        self.client.post(reverse('contratos:adicionais', args=[contrato.pk]), {
            'tipo': 'gps', 'descricao': '', 'diaria': '10.00', 'quantidade': '1',
        })
        valores = list(contrato.parcelas.order_by('numero').values_list('valor', flat=True))
        self.assertEqual(valores, [Decimal('820.00'), Decimal('390.00')])

    def test_contrato_sem_pacote_continua_pela_diaria(self):
        contrato = self.contrato_ativo(diaria=Decimal('120.00'))
        self.assertEqual(self.gerar(contrato, date(2026, 1, 1), 7), [(Decimal('840.00'), 7, True)])

    def test_contrato_antigo_mensal_sem_pacote_mantem_regra_antiga(self):
        contrato = self.contrato_ativo(diaria=Decimal('120.00'), forma_cobranca='mensal')
        self.assertEqual(self.gerar(contrato, date(2026, 1, 1), 31), [(Decimal('3720.00'), 31, True)])

    def test_prorrogacao_mantem_forma_do_contrato_e_usa_pacote(self):
        prevista = timezone.now() + timedelta(days=2)
        contrato = self.contrato_pacote(data_devolucao_prevista=prevista)
        contrato.veiculo.grupo.mensal = Decimal('2800')
        contrato.veiculo.grupo.save()
        nova = prevista + timedelta(days=14)
        self.client.post(reverse('contratos:prorrogar', args=[contrato.pk]), {
            'nova_data_devolucao': timezone.localtime(nova).strftime('%Y-%m-%dT%H:%M'),
        })
        novas = list(contrato.parcelas.filter(origem='prorrogacao').values_list('tipo', 'valor'))
        self.assertEqual(novas, [('semanal', Decimal('750.00')), ('semanal', Decimal('750.00'))])

    def test_valor_contratado_de_contrato_aberto_usa_pacote(self):
        contrato = self.criar_contrato(
            diaria=Decimal('120.00'), forma_cobranca='semanal', valor_semanal=Decimal('750.00'),
            data_devolucao_prevista=timezone.now() + timedelta(days=10),
        )
        self.assertEqual(contrato.valor_locacao_base, Decimal('1110.00'))

    def test_simulacao_de_prorrogacao_retroativa_usa_pacote(self):
        prevista = timezone.now() - timedelta(days=10)
        contrato = self.contrato_pacote(data_saida=prevista - timedelta(days=14), data_devolucao_prevista=prevista)
        simulacao = contrato.simular_prorrogacao_retroativa()
        self.assertEqual(simulacao['valor'], Decimal('1500.00'))


class FormularioContratoPacoteTests(AbertosTestBase):

    def setUp(self):
        super().setUp()
        CNHCliente.objects.create(
            cliente=self.cliente, numero='12345678900', estado_emissor='SP', categoria='B',
            validade=self.hoje + timedelta(days=365), principal=True,
        )

    def dados(self, **extra):
        base = {
            'cliente': self.cliente.pk, 'veiculo': self.veiculo.pk,
            'data_devolucao_prevista': timezone.localtime(timezone.now() + timedelta(days=10)).strftime('%Y-%m-%dT%H:%M'),
            'diaria': '100.00', 'forma_cobranca': 'semanal', 'valor_semanal': '650.00', 'valor_mensal': '',
            'km_franquia_diaria': '200', 'valor_km_excedente': '1.50', 'caucao_valor': '0', 'caucao_situacao': 'pendente',
        }
        base.update(extra)
        return base

    def test_cria_contrato_com_pacote(self):
        self.client.post(reverse('contratos:novo'), self.dados())
        contrato = Contrato.objects.get()
        self.assertEqual((contrato.forma_cobranca, contrato.valor_semanal, contrato.valor_mensal),
                         ('semanal', Decimal('650.00'), None))

    def test_bloqueia_pacote_mais_caro_que_a_diaria(self):
        resposta = self.client.post(reverse('contratos:novo'), self.dados(valor_semanal='800.00'))
        self.assertFalse(Contrato.objects.exists())
        self.assertContains(resposta, 'mais caro que 7 diárias')

    def test_checkout_gera_parcelas_pelo_pacote(self):
        self.client.post(reverse('contratos:novo'), self.dados())
        contrato = Contrato.objects.get()
        self.client.post(reverse('contratos:checkout', args=[contrato.pk]), {
            'km_saida': 1000, 'combustivel_saida': 'cheio', 'caucao_situacao': 'pago',
        })
        valores = list(contrato.parcelas.exclude(tipo='caucao').order_by('numero').values_list('valor', flat=True))
        self.assertEqual(valores[0], Decimal('650.00'))
        self.assertEqual(sum(valores), Decimal('950.00'))

    def test_checklist_mostra_primeira_parcela_pelo_pacote(self):
        self.client.post(reverse('contratos:novo'), self.dados())
        contrato = Contrato.objects.get()
        resposta = self.client.get(reverse('contratos:checkout', args=[contrato.pk]))
        self.assertContains(resposta, 'Primeira parcela (R$ 650,00)')
        self.assertContains(resposta, 'Semanal — pacote')

    def test_lista_de_veiculos_mostra_grupo_e_precos(self):
        resposta = self.client.get(reverse('contratos:novo'))
        self.assertContains(resposta, 'Grupo A · R$ 100,00/dia')

    def test_veiculo_preselecionado_carrega_tarifas(self):
        resposta = self.client.get(reverse('contratos:novo'), {'veiculo': self.veiculo.pk})
        self.assertContains(resposta, 'carregarVeiculoInicial()')
        self.assertContains(resposta, f'<option value="{self.veiculo.pk}" selected>')


class CompararTarifasPacoteTests(AbertosTestBase):

    def test_lista_parcela_pendente_de_contrato_antigo(self):
        from io import StringIO
        from django.core.management import call_command
        self.grupo.semanal = Decimal('600.00')
        self.grupo.save()
        contrato = self.contrato_ativo()
        ParcelaContrato.objects.create(
            contrato=contrato, numero=1, valor=Decimal('700.00'), dias=7,
            data_vencimento=self.hoje + timedelta(days=3),
        )
        saida = StringIO()
        call_command('comparar_tarifas_pacote', stdout=saida)
        self.assertIn('hoje R$ 700,00 → pela regra nova R$ 600,00', saida.getvalue())
        self.assertEqual(ParcelaContrato.objects.get(contrato=contrato).valor, Decimal('700.00'))


class DevolucaoPeriodoCobertoTests(ContratosTestBase):
    """Lote 1 da 2ª auditoria: uma única regra de período coberto para check-in,
    quebra de contrato, encerramento, check-out e prorrogação."""

    def setUp(self):
        super().setUp()
        self.gestor = User.objects.create_superuser('gestor_lote1', 'l1@teste.com', 'senha123')
        self.client.force_login(self.gestor)
        self.hoje = timezone.localdate()
        CNHCliente.objects.create(
            cliente=self.cliente, numero='98765432100', estado_emissor='SP',
            categoria='b', validade=self.hoje + timedelta(days=365), principal=True,
        )

    def local(self, dias, hora, minuto=0):
        dia = self.hoje + timedelta(days=dias)
        return timezone.make_aware(datetime(dia.year, dia.month, dia.day, hora, minuto))

    def ativo(self, saida, prevista):
        contrato = self.criar_contrato(
            situacao='ativo', data_saida=saida, data_devolucao_prevista=prevista,
            km_saida=1000, combustivel_saida='cheio',
        )
        gerar_parcelas(contrato, timezone.localdate(saida), contrato.fim_periodo_coberto())
        return contrato

    def checkin(self, contrato, quando, **extra):
        dados = {
            'data_devolucao_real': timezone.localtime(quando).strftime('%Y-%m-%dT%H:%M'),
            'km_devolucao': '1100', 'combustivel_devolucao': 'cheio', 'obs_devolucao': '',
        }
        dados.update(extra)
        return self.client.post(reverse('contratos:checkin', args=[contrato.pk]), dados)

    def em_cobranca(self, contrato):
        contrato = Contrato.objects.get(pk=contrato.pk)
        return contrato, contrato.parcelas.exclude(situacao='cancelada').filter(
            tipo__in=['semanal', 'acerto'],
        ).aggregate(s=Sum('valor'))['s']

    def test_devolucao_no_dia_da_semana_nova_na_mesma_hora_cancela_a_semana(self):
        contrato = self.ativo(self.local(-20, 10), self.local(-6, 10))
        self.checkin(contrato, self.local(-13, 10), motivo_encerramento='cliente_solicitou')
        contrato, total = self.em_cobranca(contrato)
        self.assertEqual(contrato.situacao, 'aguardando_devolucao')
        self.assertEqual(total, Decimal('700.00'))

    def test_devolucao_dentro_da_tolerancia_tambem_cancela_a_semana(self):
        contrato = self.ativo(self.local(-20, 10), self.local(-6, 10))
        self.checkin(contrato, self.local(-13, 10, 45), motivo_encerramento='cliente_solicitou')
        _, total = self.em_cobranca(contrato)
        self.assertEqual(total, Decimal('700.00'))

    def test_semana_comecada_fora_da_tolerancia_continua_devida(self):
        contrato = self.ativo(self.local(-20, 10), self.local(-6, 10))
        self.checkin(contrato, self.local(-13, 15))
        contrato, total = self.em_cobranca(contrato)
        self.assertEqual(contrato.situacao, 'aguardando_devolucao')
        self.assertEqual(total, Decimal('1400.00'))
        self.assertEqual(contrato.dias_extras, 0)

    def test_devolucao_no_meio_da_segunda_semana_cancela_so_a_terceira(self):
        contrato = self.ativo(self.local(-25, 10), self.local(-4, 10))
        self.checkin(contrato, self.local(-15, 10), motivo_encerramento='cliente_solicitou')
        contrato, total = self.em_cobranca(contrato)
        self.assertEqual(total, Decimal('1400.00'))
        self.assertEqual(contrato.parcelas.filter(situacao='cancelada').count(), 1)

    def test_hora_prevista_depois_da_hora_de_saida_cobra_diaria_extra(self):
        contrato = self.ativo(self.local(-10, 8), self.local(-3, 20))
        self.assertEqual(contrato.limite_devolucao_sem_extra(), self.local(-3, 9))
        self.checkin(contrato, self.local(-3, 20))
        contrato, total = self.em_cobranca(contrato)
        self.assertEqual(contrato.dias_extras, 1)
        self.assertEqual(total, Decimal('800.00'))

    def test_hora_prevista_depois_da_saida_sem_atraso_real_nao_cobra_extra(self):
        contrato = self.ativo(self.local(-10, 8), self.local(-3, 20))
        self.checkin(contrato, self.local(-3, 8, 50))
        contrato, total = self.em_cobranca(contrato)
        self.assertEqual(contrato.dias_extras, 0)
        self.assertEqual(total, Decimal('700.00'))

    def test_checkin_recusa_data_futura_e_data_antes_da_saida(self):
        contrato = self.ativo(self.local(-3, 10), self.local(4, 10))
        for quando in (timezone.now() + timedelta(days=30), self.local(-4, 10)):
            resposta = self.checkin(contrato, quando, motivo_encerramento='cliente_solicitou')
            self.assertEqual(resposta.status_code, 200)
            self.assertIn('data_devolucao_real', resposta.context['form'].errors)
        self.assertEqual(Contrato.objects.get(pk=contrato.pk).situacao, 'ativo')

    def test_devolucao_antecipada_exige_motivo_e_fica_no_historico(self):
        from .models import HistoricoContrato
        contrato = self.ativo(self.local(-8, 10), self.local(13, 10))

        resposta = self.checkin(contrato, self.local(-1, 10))
        self.assertIn('motivo_encerramento', resposta.context['form'].errors)
        self.assertEqual(Contrato.objects.get(pk=contrato.pk).situacao, 'ativo')

        self.checkin(contrato, self.local(-1, 10), motivo_encerramento='problema_mecanico')
        historico = HistoricoContrato.objects.get(contrato=contrato, acao='devolucao_antecipada')
        self.assertEqual(historico.usuario, self.gestor)
        self.assertEqual(historico.dados['parcelas_canceladas'], [2, 3])
        self.assertEqual(historico.dados['valor_cancelado'], '1400.00')
        self.assertIn('Problema Mecânico', historico.motivo)

    def test_checkin_no_prazo_nao_pede_motivo(self):
        contrato = self.ativo(self.local(-10, 10), self.local(-3, 10))
        self.checkin(contrato, self.local(-3, 10))
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(contrato.situacao, 'aguardando_devolucao')
        self.assertEqual(contrato.motivo_encerramento, '')

    def test_retroativo_devolvido_antes_do_proximo_vencimento_cancela_a_parcela(self):
        contrato = self.criar_contrato(
            situacao='ativo', origem_retroativa=True, data_saida=self.local(-30, 10),
            data_devolucao_prevista=self.local(10, 10), km_saida=1000, combustivel_saida='cheio',
        )
        gerar_parcelas(contrato, self.hoje + timedelta(days=5), contrato.fim_periodo_coberto())
        self.checkin(contrato, self.local(-1, 10), motivo_encerramento='cliente_solicitou')
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(contrato.situacao, 'aguardando_devolucao')
        self.assertFalse(contrato.parcelas.exclude(situacao='cancelada').exists())

    def test_prorrogacao_sem_encargos_mantem_o_periodo_real_da_parcela(self):
        contrato = self.ativo(self.local(-20, 10), self.local(-6, 10))
        self.client.post(reverse('contratos:prorrogar', args=[contrato.pk]), {
            'nova_data_devolucao': timezone.localtime(self.local(15, 10)).strftime('%Y-%m-%dT%H:%M'),
            'encargos_retroativos': 'nao',
        })
        novas = list(contrato.parcelas.filter(origem='prorrogacao').order_by('numero'))
        self.assertEqual(novas[0].data_vencimento, self.hoje)
        self.assertEqual(novas[0].inicio_periodo, self.hoje - timedelta(days=6))
        self.checkin(contrato, self.local(-2, 10), motivo_encerramento='cliente_solicitou')
        novas[0].refresh_from_db()
        self.assertNotEqual(novas[0].situacao, 'cancelada')

    def test_quebra_de_contrato_usa_a_mesma_regra(self):
        contrato = self.ativo(self.local(-20, 10), self.local(-6, 10))
        self.client.post(reverse('contratos:encerramento-antecipado', args=[contrato.pk]), {
            'data_devolucao_real': timezone.localtime(self.local(-13, 10)).strftime('%Y-%m-%dT%H:%M'),
            'km_devolucao': '1100', 'combustivel_devolucao': 'cheio',
            'motivo_encerramento': 'cliente_solicitou',
        })
        contrato, total = self.em_cobranca(contrato)
        self.assertEqual(contrato.situacao, 'encerrado')
        self.assertEqual(total, Decimal('700.00'))

    def test_checkout_com_devolucao_no_mesmo_dia_cobra_uma_diaria(self):
        prevista = timezone.localtime() + timedelta(minutes=30)
        contrato = self.criar_contrato(data_devolucao_prevista=prevista)
        self.client.post(reverse('contratos:checkout', args=[contrato.pk]), {
            'data_devolucao_prevista': prevista.strftime('%Y-%m-%dT%H:%M'),
            'km_saida': '1000', 'combustivel_saida': 'cheio', 'obs_saida': '', 'caucao_situacao': 'pendente',
        })
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(contrato.situacao, 'ativo')
        self.assertEqual(list(contrato.parcelas.values_list('valor', 'dias')), [(Decimal('100.00'), 1)])

    def test_checkout_com_devolucao_prevista_vencida_pede_nova_data(self):
        contrato = self.criar_contrato()
        Contrato.objects.filter(pk=contrato.pk).update(data_devolucao_prevista=timezone.now() - timedelta(days=2))
        dados = {'km_saida': '1000', 'combustivel_saida': 'cheio', 'obs_saida': '', 'caucao_situacao': 'pendente'}
        resposta = self.client.post(reverse('contratos:checkout', args=[contrato.pk]), dados)
        self.assertIn('data_devolucao_prevista', resposta.context['form'].errors)
        self.assertEqual(Contrato.objects.get(pk=contrato.pk).situacao, 'aberto')

        nova = timezone.localtime() + timedelta(days=7)
        dados['data_devolucao_prevista'] = nova.strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('contratos:checkout', args=[contrato.pk]), dados)
        contrato = Contrato.objects.get(pk=contrato.pk)
        self.assertEqual(contrato.situacao, 'ativo')
        self.assertEqual(contrato.parcelas.count(), 1)

    def test_prorrogacao_de_contrato_de_um_dia_nao_cobra_o_dia_duas_vezes(self):
        contrato = self.ativo(self.local(-1, 8), self.local(-1, 20))
        self.assertEqual(contrato.parcelas.count(), 1)
        self.client.post(reverse('contratos:prorrogar', args=[contrato.pk]), {
            'nova_data_devolucao': timezone.localtime(self.local(6, 8)).strftime('%Y-%m-%dT%H:%M'),
            'encargos_retroativos': 'sim',
        })
        dias = sum(contrato.parcelas.values_list('dias', flat=True))
        self.assertEqual(dias, 7)

    def test_conferencia_aponta_semana_cobrada_depois_da_devolucao(self):
        from io import StringIO
        from django.core.management import call_command
        contrato = self.ativo(self.local(-20, 10), self.local(-6, 10))
        Contrato.objects.filter(pk=contrato.pk).update(
            situacao='encerrado', data_devolucao_real=self.local(-13, 10),
        )
        saida = StringIO()
        call_command('conferir_integridade_financeira', stdout=saida)
        self.assertIn(f'{contrato.numero}: parcela 2', saida.getvalue())


class ValoresMinimosAvariaAdicionalTests(TestCase):

    def test_avaria_negativa_e_recusada_e_zerada_e_aceita(self):
        from .forms import AvariaContratoForm
        negativa = AvariaContratoForm(data={'descricao': 'Risco', 'valor_cobrado': '-500', 'situacao': 'identificada'})
        zerada = AvariaContratoForm(data={'descricao': 'Risco', 'valor_cobrado': '0', 'situacao': 'isenta'})
        self.assertFalse(negativa.is_valid())
        self.assertIn('valor_cobrado', negativa.errors)
        self.assertTrue(zerada.is_valid(), zerada.errors)

    def test_adicional_com_diaria_zero_ou_quantidade_zero_e_recusado(self):
        from .forms import AdicionalContratoForm
        diaria_zero = AdicionalContratoForm(data={'tipo': 'gps', 'diaria': '0', 'quantidade': '1'})
        diaria_negativa = AdicionalContratoForm(data={'tipo': 'gps', 'diaria': '-50', 'quantidade': '1'})
        quantidade_zero = AdicionalContratoForm(data={'tipo': 'gps', 'diaria': '20', 'quantidade': '0'})
        valido = AdicionalContratoForm(data={'tipo': 'gps', 'diaria': '20', 'quantidade': '1'})
        self.assertIn('diaria', diaria_zero.errors)
        self.assertIn('diaria', diaria_negativa.errors)
        self.assertIn('quantidade', quantidade_zero.errors)
        self.assertTrue(valido.is_valid(), valido.errors)


class MigracaoSemanaCorrenteTests(AbertosTestBase):

    dados = FormularioContratoPacoteTests.dados

    def setUp(self):
        super().setUp()
        CNHCliente.objects.create(
            cliente=self.cliente, numero='12345678900', estado_emissor='SP', categoria='B',
            validade=self.hoje + timedelta(days=365), principal=True,
        )

    def dados_retroativo(self, **extra):
        saida = timezone.localtime(timezone.now() - timedelta(days=11))
        dados = self.dados(
            data_devolucao_prevista=timezone.localtime(timezone.now() + timedelta(days=30)).strftime('%Y-%m-%dT%H:%M'),
            data_saida=saida.strftime('%Y-%m-%dT%H:%M'), km_saida='1000', combustivel_saida='cheio',
            justificativa_retroativa='Contrato controlado na planilha.', forma_pagamento_regularizacao='pix',
        )
        dados.update(extra)
        return dados

    def contrato_criado(self):
        return Contrato.objects.get(origem_retroativa=True)

    def test_regularizacao_usa_a_forma_informada_e_aparece_separada_no_fluxo(self):
        self.client.post(reverse('contratos:novo'), self.dados_retroativo(
            valor_ja_recebido='1300.00', forma_pagamento_regularizacao='transferencia',
        ))
        contrato = self.contrato_criado()
        self.assertEqual(PagamentoContrato.objects.get(contrato=contrato, tipo='regularizacao').forma_pagamento, 'transferencia')

        fluxo = self.client.get(reverse('financeiro:fluxo'), {'ano': self.hoje.year, 'mes': self.hoje.month}).context
        self.assertEqual(fluxo['total_regularizacao'], Decimal('1300.00'))
        self.assertEqual(fluxo['total_entradas_do_mes'], fluxo['total_entradas'] - Decimal('1300.00'))

    def test_valor_recebido_exige_a_forma_de_pagamento(self):
        resposta = self.client.post(reverse('contratos:novo'), self.dados_retroativo(
            valor_ja_recebido='1300.00', forma_pagamento_regularizacao='',
        ))
        self.assertFalse(Contrato.objects.filter(origem_retroativa=True).exists())
        self.assertIn('forma_pagamento_regularizacao', resposta.context['form'].errors)

    def test_dias_ate_o_proximo_vencimento_viram_parcela_proporcional_vencendo_hoje(self):
        self.client.post(reverse('contratos:novo'), self.dados_retroativo(valor_ja_recebido='1300.00'))

        contrato = self.contrato_criado()
        parcial = contrato.parcelas.get(tipo='semanal', inicio_periodo=self.hoje)
        self.assertEqual(parcial.data_vencimento, self.hoje)
        self.assertEqual(parcial.dias, 3)
        self.assertEqual(parcial.valor, Decimal('300.00'))
        self.assertEqual(parcial.situacao, 'pendente')
        proxima = contrato.parcelas.filter(tipo='semanal').exclude(pk=parcial.pk).order_by('data_vencimento').first()
        self.assertEqual(proxima.data_vencimento, self.hoje + timedelta(days=3))
        regularizacao = PagamentoContrato.objects.get(contrato=contrato, tipo='regularizacao')
        self.assertEqual(regularizacao.valor, Decimal('1300.00'))
        self.assertIn(f'a {self.hoje - timedelta(days=1):%d/%m/%Y}', regularizacao.observacoes)

    def test_dias_ja_pagos_saem_da_regularizacao_sem_contar_o_dinheiro_duas_vezes(self):
        self.client.post(reverse('contratos:novo'), self.dados_retroativo(
            valor_ja_recebido='1600.00', dias_correntes_pagos='on',
        ))

        contrato = self.contrato_criado()
        parcial = contrato.parcelas.get(tipo='semanal', inicio_periodo=self.hoje)
        self.assertEqual(parcial.situacao, 'pago')
        recebido = PagamentoContrato.objects.filter(contrato=contrato).exclude(tipo='caucao')
        self.assertEqual(sum(p.valor for p in recebido), Decimal('1600.00'))
        self.assertEqual(recebido.get(tipo='regularizacao').valor, Decimal('1300.00'))

    def test_dias_marcados_como_pagos_exigem_valor_recebido_suficiente(self):
        resposta = self.client.post(reverse('contratos:novo'), self.dados_retroativo(
            valor_ja_recebido='100.00', dias_correntes_pagos='on',
        ))

        self.assertFalse(Contrato.objects.filter(origem_retroativa=True).exists())
        self.assertIn('valor_ja_recebido', resposta.context['form'].errors)

    def test_migracao_no_dia_do_vencimento_nao_gera_parcela_proporcional(self):
        saida = timezone.localtime(timezone.now() - timedelta(days=14))
        self.client.post(reverse('contratos:novo'), self.dados_retroativo(
            data_saida=saida.strftime('%Y-%m-%dT%H:%M'), valor_ja_recebido='1300.00',
        ))

        contrato = self.contrato_criado()
        primeira = contrato.parcelas.filter(tipo='semanal').order_by('data_vencimento').first()
        self.assertEqual(primeira.data_vencimento, self.hoje)
        self.assertEqual(primeira.dias, 7)


class VencimentoMensalAncoradoTests(TestCase):

    def test_inicio_no_dia_31_volta_ao_31_nos_meses_que_tem(self):
        from datetime import date
        from .models import ciclos_de_cobranca
        ciclos = ciclos_de_cobranca(date(2027, 1, 31), date(2027, 6, 1), 'mensal')
        self.assertEqual(
            [inicio for inicio, _, _ in ciclos],
            [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)],
        )
        self.assertTrue(all(completo for _, _, completo in ciclos[:-1]))

    def test_inicio_em_29_de_fevereiro(self):
        from datetime import date
        from .models import ciclos_de_cobranca, proximo_vencimento_contratual
        ciclos = ciclos_de_cobranca(date(2028, 2, 29), date(2028, 5, 29), 'mensal')
        self.assertEqual([i for i, _, _ in ciclos], [date(2028, 2, 29), date(2028, 3, 29), date(2028, 4, 29)])
        self.assertEqual(proximo_vencimento_contratual(date(2027, 1, 31), date(2027, 3, 2), 'mensal'), date(2027, 3, 31))

    def test_prorrogacao_mensal_continua_ancorada_na_saida(self):
        from datetime import date
        from .models import ciclos_de_cobranca
        ciclos = ciclos_de_cobranca(date(2027, 4, 30), date(2027, 7, 31), 'mensal', ancora=date(2027, 1, 31))
        self.assertEqual([i for i, _, _ in ciclos], [date(2027, 4, 30), date(2027, 5, 31), date(2027, 6, 30)])
        self.assertTrue(all(completo for _, _, completo in ciclos))

    def test_semanal_nao_muda(self):
        from datetime import date
        from .models import ciclos_de_cobranca
        ciclos = ciclos_de_cobranca(date(2027, 1, 3), date(2027, 1, 20), 'semanal')
        self.assertEqual(ciclos, [(date(2027, 1, 3), 7, True), (date(2027, 1, 10), 7, True), (date(2027, 1, 17), 3, False)])


class AtrasoETicketMedioTests(AbertosTestBase):

    def test_contrato_em_fechamento_nao_aparece_como_atrasado(self):
        agora = timezone.now()
        ativo = self.contrato_ativo(data_devolucao_prevista=agora - timedelta(days=2))
        fechamento = self.criar_contrato(
            situacao='aguardando_devolucao', data_saida=agora - timedelta(days=10),
            data_devolucao_prevista=agora - timedelta(days=2), data_devolucao_real=agora - timedelta(days=1),
        )
        self.assertTrue(ativo.em_atraso)
        self.assertFalse(fechamento.em_atraso)
        atrasados = self.client.get(reverse('core:dashboard')).context['contratos_atraso']
        self.assertEqual([c.pk for c in atrasados], [ativo.pk])

    def test_ticket_medio_usa_so_a_receita_dos_encerrados(self):
        encerrado = self.criar_contrato(situacao='encerrado')
        ativo = self.contrato_ativo()
        PagamentoContrato.objects.create(contrato=encerrado, forma_pagamento='pix', tipo='locacao', valor=Decimal('700.00'))
        PagamentoContrato.objects.create(contrato=ativo, forma_pagamento='pix', tipo='locacao', valor=Decimal('1400.00'))

        contexto = self.client.get(reverse('core:relatorio-contratos')).context

        self.assertEqual(contexto['ticket_medio'], Decimal('700.00'))

    def test_texto_do_acerto_mostra_a_diaria_com_adicionais(self):
        from .views import _gerar_parcela_acerto_se_necessario
        contrato = self.contrato_ativo()
        contrato.adicionais.create(tipo='gps', diaria=Decimal('20.00'), quantidade=1)
        contrato.dias_extras = 2
        contrato.valor_dias_extras = contrato.diaria_com_adicionais * 2
        _gerar_parcela_acerto_se_necessario(contrato)
        texto = contrato.parcelas.get(tipo='acerto').observacoes
        self.assertIn('2 dia(s) extra(s) × R$ 120,00', texto)
        self.assertIn('R$ 100,00 do veículo + R$ 20,00 de adicionais', texto)
        self.assertIn('= R$ 240,00', texto)


class ValorAtualizadoEPdfTests(AbertosTestBase):

    def setUp(self):
        super().setUp()
        self.config.percentual_multa_atraso = Decimal('2.00')
        self.config.percentual_juros_diario = Decimal('0.10')
        self.config.save()

    def contrato_com_atraso(self):
        from apps.financeiro.models import ContaReceber
        contrato = self.contrato_ativo()
        vencida = self.parcela(contrato, 1, -10, '700.00', situacao='em_atraso')
        self.parcela(contrato, 2, 5, '700.00')
        contrato.sincronizar_conta_receber()
        return contrato, vencida, ContaReceber.objects.get(contrato=contrato)

    def test_conta_mostra_valor_com_multa_e_juros(self):
        contrato, vencida, conta = self.contrato_com_atraso()
        encargos = vencida.valor_corrigido - vencida.valor
        self.assertGreater(encargos, 0)

        lista = self.client.get(reverse('financeiro:contas-receber')).context
        linha = next(c for c in lista['contas'] if c.pk == conta.pk)
        self.assertEqual(linha.encargos_atraso, encargos)
        self.assertEqual(linha.valor_atualizado, conta.valor_saldo + encargos)
        self.assertEqual(lista['kpi_vencido_atualizado'], lista['kpi_vencido'] + encargos)

        detalhe = self.client.get(reverse('financeiro:conta-receber-detalhe', args=[conta.pk])).context
        self.assertEqual(detalhe['form'].initial['valor'], conta.valor_saldo + encargos)

        inadimplencia = self.client.get(reverse('core:relatorio-inadimplencia')).context
        self.assertEqual(inadimplencia['total_vencido_atualizado'], inadimplencia['total_vencido'] + encargos)

    def test_receber_o_valor_atualizado_quita_as_parcelas_sem_deixar_resto(self):
        contrato, vencida, conta = self.contrato_com_atraso()
        valor = vencida.valor_corrigido + Decimal('700.00')
        self.client.post(reverse('financeiro:receber', args=[conta.pk]), {
            'tipo': 'locacao', 'valor': str(valor), 'forma_pagamento': 'pix',
            'data_pagamento': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertFalse(contrato.parcelas.filter(situacao__in=['pendente', 'em_atraso']).exists())

    def test_pdf_depois_do_check_out_mostra_o_mesmo_total_do_detalhe(self):
        from django.template.loader import render_to_string
        contrato, _, _ = self.contrato_com_atraso()
        contrato = Contrato.objects.get(pk=contrato.pk)
        html = render_to_string('contracts/pdf_contrato.html', {'contrato': contrato})
        total = f'{contrato.total_geral:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        self.assertIn(total, html)
        self.assertNotIn('(estimado)', html)
        self.assertIn('Locação (parcelas)', html)

    def test_pdf_antes_do_check_out_e_estimado(self):
        from django.template.loader import render_to_string
        contrato = self.criar_contrato()
        html = render_to_string('contracts/pdf_contrato.html', {'contrato': contrato})
        self.assertIn('(estimado)', html)




class AjusteAvariaERemocaoAdicionalTests(AbertosTestBase):

    def test_edita_valor_e_situacao_da_avaria_e_registra_no_historico(self):
        from .models import HistoricoContrato
        contrato = self.contrato_ativo()
        avaria = contrato.avarias.create(descricao='Retrovisor', valor_cobrado=Decimal('0.00'), situacao='identificada')

        self.client.post(
            reverse('contratos:avaria-ajustar', args=[contrato.pk, avaria.pk]),
            {'valor_cobrado': '250.00', 'situacao': 'cobrada'},
        )

        avaria.refresh_from_db()
        self.assertEqual((avaria.valor_cobrado, avaria.situacao), (Decimal('250.00'), 'cobrada'))
        self.assertEqual(Contrato.objects.get(pk=contrato.pk).total_avarias, Decimal('250.00'))
        registro = HistoricoContrato.objects.get(contrato=contrato, acao='ajuste_avaria')
        self.assertEqual(registro.usuario, self.user)
        self.assertEqual(registro.dados['antes'], {'valor_cobrado': '0.00', 'situacao': 'identificada'})
        self.assertEqual(registro.dados['depois'], {'valor_cobrado': '250.00', 'situacao': 'cobrada'})

    def test_avaria_paga_nao_pode_ser_editada(self):
        contrato = self.contrato_ativo()
        avaria = contrato.avarias.create(descricao='Farol', valor_cobrado=Decimal('300.00'), situacao='paga')

        self.client.post(
            reverse('contratos:avaria-ajustar', args=[contrato.pk, avaria.pk]),
            {'valor_cobrado': '10.00', 'situacao': 'isenta'},
        )

        avaria.refresh_from_db()
        self.assertEqual((avaria.valor_cobrado, avaria.situacao), (Decimal('300.00'), 'paga'))

    def test_ajuste_recusa_valor_negativo_e_situacao_paga(self):
        contrato = self.contrato_ativo()
        avaria = contrato.avarias.create(descricao='Risco', valor_cobrado=Decimal('100.00'), situacao='cobrada')
        url = reverse('contratos:avaria-ajustar', args=[contrato.pk, avaria.pk])

        self.client.post(url, {'valor_cobrado': '-50.00', 'situacao': 'cobrada'})
        self.client.post(url, {'valor_cobrado': '100.00', 'situacao': 'paga'})

        avaria.refresh_from_db()
        self.assertEqual((avaria.valor_cobrado, avaria.situacao), (Decimal('100.00'), 'cobrada'))
        self.assertFalse(PagamentoContrato.objects.filter(avaria=avaria).exists())

    def test_remover_adicional_recalcula_so_as_parcelas_futuras_em_aberto(self):
        from .models import HistoricoContrato
        contrato = self.contrato_ativo()
        adicional = contrato.adicionais.create(tipo='gps', diaria=Decimal('20.00'), quantidade=1)
        paga = ParcelaContrato.objects.create(
            contrato=contrato, numero=1, valor=Decimal('840.00'), dias=7, periodo_completo=True,
            data_vencimento=self.hoje - timedelta(days=7), situacao='pago',
        )
        futura = ParcelaContrato.objects.create(
            contrato=contrato, numero=2, valor=Decimal('840.00'), dias=7, periodo_completo=True,
            data_vencimento=self.hoje + timedelta(days=7),
        )

        self.client.post(reverse('contratos:adicional-remover', args=[contrato.pk, adicional.pk]))

        paga.refresh_from_db()
        futura.refresh_from_db()
        self.assertFalse(contrato.adicionais.exists())
        self.assertEqual(paga.valor, Decimal('840.00'))
        self.assertEqual(futura.valor, Decimal('700.00'))
        self.assertEqual(HistoricoContrato.objects.get(contrato=contrato, acao='remocao_adicional').dados['tipo'], 'GPS')

    def test_nao_remove_adicional_depois_do_check_in(self):
        contrato = self.contrato_ativo()
        adicional = contrato.adicionais.create(tipo='gps', diaria=Decimal('20.00'), quantidade=1)
        Contrato.objects.filter(pk=contrato.pk).update(situacao='aguardando_devolucao')

        self.client.post(reverse('contratos:adicional-remover', args=[contrato.pk, adicional.pk]))

        self.assertTrue(contrato.adicionais.filter(pk=adicional.pk).exists())

    def test_telas_mostram_os_botoes(self):
        contrato = self.contrato_ativo()
        contrato.avarias.create(descricao='Risco', valor_cobrado=Decimal('100.00'), situacao='cobrada')
        contrato.adicionais.create(tipo='gps', diaria=Decimal('20.00'), quantidade=1)
        self.assertContains(self.client.get(reverse('contratos:avarias', args=[contrato.pk])), "abrirAjusteAvaria(")
        self.assertContains(self.client.get(reverse('contratos:adicionais', args=[contrato.pk])), 'Remover')
