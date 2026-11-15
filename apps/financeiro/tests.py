import time
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.contracts.models import Contrato, PagamentoContrato, ParcelaContrato
from apps.customers.models import Cliente
from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo

from .forms import ConfiguracaoLocadoraForm
from .models import (
    ConfiguracaoLocadora, ContaReceber, DespesaOperacional, MultaTransito,
    NotificacaoCobranca, ParcelaDespesa, gerar_parcelas_despesa,
)
from .pix import crc16, gerar_payload, qr_code_svg


class FinanceiroTestBase(TestCase):
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
            data_emissao=timezone.localdate(),
            data_vencimento=timezone.localdate() + timedelta(days=10),
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
            data_emissao=timezone.localdate() - timedelta(days=20),
            data_vencimento=timezone.localdate() - timedelta(days=5),
        )

        conta.atualizar_situacao()

        self.assertEqual(conta.situacao, 'vencido')
        self.assertEqual(conta.dias_em_atraso, 5)

    def test_marca_como_vencido_mesmo_com_pagamento_parcial(self):
        contrato = self.criar_contrato()
        conta = self.criar_conta_receber(
            contrato, valor_total=Decimal('700.00'),
            data_emissao=timezone.localdate() - timedelta(days=20),
            data_vencimento=timezone.localdate() - timedelta(days=5),
        )
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('300.00')
        )

        conta.atualizar_situacao()

        self.assertEqual(conta.situacao, 'vencido')
        self.assertEqual(conta.valor_pago, Decimal('300.00'))
        self.assertEqual(conta.valor_saldo, Decimal('400.00'))

    def test_conta_quitada_vencida_volta_para_vencido_quando_total_aumenta(self):
        contrato = self.criar_contrato()
        conta = self.criar_conta_receber(
            contrato, valor_total=Decimal('700.00'),
            data_emissao=timezone.localdate() - timedelta(days=20),
            data_vencimento=timezone.localdate() - timedelta(days=5),
        )
        PagamentoContrato.objects.create(
            contrato=contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('700.00')
        )
        conta.refresh_from_db()
        self.assertEqual(conta.situacao, 'pago')

        contrato.avarias.create(
            descricao='Para-choque', valor_cobrado=Decimal('180.00'), situacao='cobrada',
        )
        conta.refresh_from_db()

        self.assertEqual(conta.valor_total, Decimal('880.00'))
        self.assertEqual(conta.situacao, 'vencido')
        self.assertEqual(conta.valor_saldo, Decimal('180.00'))

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
        novo_vencimento = timezone.localdate() + timedelta(days=20)
        Contrato.objects.filter(pk=contrato.pk).update(
            data_devolucao_prevista=contrato.criado_em + timedelta(days=10),
        )

        conta.atualizar_situacao(novo_vencimento=novo_vencimento)

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
            data_competencia=timezone.localdate(),
        )

        self.assertFalse(despesa.pago)

        despesa.data_pagamento = timezone.localdate()
        despesa.save()

        self.assertTrue(despesa.pago)

    def test_despesa_parcelada_so_fica_paga_com_todas_as_parcelas_pagas(self):
        despesa = DespesaOperacional.objects.create(
            categoria='seguro', descricao='Seguro frota', valor=Decimal('600.00'),
            data_competencia=timezone.localdate(), parcelado=True, numero_parcelas=2,
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
            data_competencia=timezone.localdate(), debito_automatico=True,
        )
        parcela = ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('1200.00'),
            data_vencimento=timezone.localdate() - timedelta(days=1), situacao='pendente',
        )
        outra_despesa = DespesaOperacional.objects.create(
            categoria='salario', descricao='Salário atendente', valor=Decimal('2000.00'),
            data_competencia=timezone.localdate(), debito_automatico=False,
        )
        parcela_manual = ParcelaDespesa.objects.create(
            despesa=outra_despesa, numero=1, valor=Decimal('2000.00'),
            data_vencimento=timezone.localdate() - timedelta(days=1), situacao='pendente',
        )

        confirmadas = DespesaOperacional.sincronizar_auto_pagamento()

        parcela.refresh_from_db()
        parcela_manual.refresh_from_db()
        self.assertEqual(confirmadas, 1)
        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(parcela.data_pagamento, parcela.data_vencimento)
        self.assertEqual(parcela_manual.situacao, 'pendente')


class ParcelaDespesaTests(FinanceiroTestBase):
    """Cobre ParcelaDespesa.em_atraso / dias_atraso (usados na agenda de pagamentos)."""

    def test_em_atraso_e_dias_atraso_para_parcela_pendente_vencida(self):
        despesa = DespesaOperacional.objects.create(
            categoria='aluguel', descricao='Aluguel escritório', valor=Decimal('1200.00'),
            data_competencia=timezone.localdate(),
        )
        parcela = ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('1200.00'),
            data_vencimento=timezone.localdate() - timedelta(days=3), situacao='pendente',
        )

        self.assertTrue(parcela.em_atraso)
        self.assertEqual(parcela.dias_atraso, 3)

    def test_parcela_paga_nao_esta_em_atraso(self):
        despesa = DespesaOperacional.objects.create(
            categoria='aluguel', descricao='Aluguel escritório', valor=Decimal('1200.00'),
            data_competencia=timezone.localdate(),
        )
        parcela = ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('1200.00'),
            data_vencimento=timezone.localdate() - timedelta(days=3),
            situacao='pago', data_pagamento=timezone.localdate(),
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
            data_infracao=timezone.localdate() - timedelta(days=100),
            descricao='Estacionamento irregular', valor=Decimal('88.38'),
        )

        self.assertIsNone(multa.contrato)

    def test_prazo_critico_e_vencido(self):
        hoje = timezone.localdate()
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


class PixPayloadTests(TestCase):
    EXEMPLO_BCB = (
        '00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000'
        '5204000053039865802BR5913Fulano de Tal6008BRASILIA62070503***63041D3D'
    )

    def test_crc16_confere_com_vetor_padrao(self):
        self.assertEqual(crc16('123456789'), '29B1')

    def test_payload_estatico_igual_ao_exemplo_do_banco_central(self):
        payload = gerar_payload('123e4567-e12b-12d1-a456-426655440000', 'Fulano de Tal', 'BRASILIA')
        self.assertEqual(payload, self.EXEMPLO_BCB)

    def test_payload_com_valor_e_txid(self):
        payload = gerar_payload('12345678909', 'Locadora', 'Sao Paulo', Decimal('450'), 'AF-2026-0012P07')
        self.assertIn('5406450.00', payload)
        self.assertIn('62170513AF20260012P07', payload)
        self.assertEqual(payload[-4:], crc16(payload[:-4]))

    def test_nome_e_cidade_sem_acento_e_truncados(self):
        payload = gerar_payload('12345678909', 'Locação Fácil Veículos e Serviços', 'São José dos Campos')
        self.assertIn('5925Locacao Facil Veiculos e', payload)
        self.assertIn('6015Sao Jose dos Ca', payload)

    def test_valor_zero_e_recusado(self):
        with self.assertRaises(ValueError):
            gerar_payload('12345678909', 'Locadora', 'Sao Paulo', Decimal('0'))

    def test_qr_code_svg_gerado(self):
        self.assertIn('<svg', qr_code_svg(self.EXEMPLO_BCB))


class PixParcelaTests(FinanceiroTestBase):

    def setUp(self):
        super().setUp()
        self.config = ConfiguracaoLocadora.obter()
        self.config.pix_tipo_chave = 'cpf'
        self.config.pix_chave = '12345678909'
        self.config.pix_nome_recebedor = 'Aluga Facil'
        self.config.pix_cidade_recebedor = 'Sao Paulo'
        self.config.save()
        self.contrato = self.criar_contrato()

    def criar_parcela(self, **kwargs):
        dados = dict(
            contrato=self.contrato, numero=3, valor=Decimal('700.00'),
            data_vencimento=timezone.localdate() + timedelta(days=1),
        )
        dados.update(kwargs)
        return ParcelaContrato.objects.create(**dados)

    def test_config_sem_pix_nao_gera_codigo(self):
        self.config.pix_chave = ''
        self.config.save()
        self.assertIsNone(self.criar_parcela().pix_copia_e_cola())

    def test_txid_usa_numero_do_contrato_e_da_parcela(self):
        parcela = self.criar_parcela()
        esperado = self.contrato.numero.replace('-', '') + 'P03'
        self.assertEqual(parcela.txid_pix, esperado)

    def test_codigo_usa_valor_da_parcela_e_txid(self):
        parcela = self.criar_parcela()
        codigo = parcela.pix_copia_e_cola()
        self.assertIn('5406700.00', codigo)
        self.assertIn(parcela.txid_pix, codigo)

    def test_parcela_atrasada_usa_valor_corrigido(self):
        parcela = self.criar_parcela(
            situacao='em_atraso', data_vencimento=timezone.localdate() - timedelta(days=5)
        )
        self.assertGreater(parcela.valor_corrigido, parcela.valor)
        self.assertIn(f'{parcela.valor_corrigido:.2f}', parcela.pix_copia_e_cola())

    def test_parcela_paga_nao_gera_codigo(self):
        self.assertIsNone(self.criar_parcela(situacao='pago').pix_copia_e_cola())


class ConfiguracaoPixFormTests(TestCase):
    DADOS_BASE = {
        'percentual_multa_atraso': '2.00',
        'percentual_juros_diario': '0.0333',
        'dias_carencia': '0',
        'custo_reposicao_combustivel': '0',
        'tolerancia_devolucao_minutos': '60',
        'pix_nome_recebedor': 'Aluga Facil',
        'pix_cidade_recebedor': 'Sao Paulo',
    }

    def validar(self, **dados):
        form = ConfiguracaoLocadoraForm(
            data={**self.DADOS_BASE, **dados}, instance=ConfiguracaoLocadora.obter()
        )
        return form, form.is_valid()

    def test_sem_pix_continua_valido(self):
        dados = {k: v for k, v in self.DADOS_BASE.items() if not k.startswith('pix_')}
        form = ConfiguracaoLocadoraForm(data=dados, instance=ConfiguracaoLocadora.obter())
        self.assertTrue(form.is_valid(), form.errors)

    def test_cpf_e_salvo_so_com_digitos(self):
        form, valido = self.validar(pix_tipo_chave='cpf', pix_chave='123.456.789-09')
        self.assertTrue(valido, form.errors)
        self.assertEqual(form.cleaned_data['pix_chave'], '12345678909')

    def test_cpf_invalido_e_recusado(self):
        form, valido = self.validar(pix_tipo_chave='cpf', pix_chave='111.111.111-11')
        self.assertFalse(valido)
        self.assertIn('pix_chave', form.errors)

    def test_telefone_recebe_prefixo_55(self):
        form, valido = self.validar(pix_tipo_chave='telefone', pix_chave='(11) 98765-4321')
        self.assertTrue(valido, form.errors)
        self.assertEqual(form.cleaned_data['pix_chave'], '+5511987654321')

    def test_email_e_salvo_em_minusculas(self):
        form, valido = self.validar(pix_tipo_chave='email', pix_chave='Financeiro@Locadora.com')
        self.assertTrue(valido, form.errors)
        self.assertEqual(form.cleaned_data['pix_chave'], 'financeiro@locadora.com')

    def test_chave_aleatoria_invalida_e_recusada(self):
        form, valido = self.validar(pix_tipo_chave='aleatoria', pix_chave='abc123')
        self.assertFalse(valido)
        self.assertIn('pix_chave', form.errors)

    def test_chave_sem_tipo_e_recusada(self):
        form, valido = self.validar(pix_chave='12345678909')
        self.assertFalse(valido)
        self.assertIn('pix_tipo_chave', form.errors)

    def test_tipo_sem_nome_do_recebedor_e_recusado(self):
        form, valido = self.validar(
            pix_tipo_chave='cpf', pix_chave='12345678909', pix_nome_recebedor=''
        )
        self.assertFalse(valido)
        self.assertIn('pix_nome_recebedor', form.errors)


class ConfiguracaoPixViewTests(TestCase):

    def setUp(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_pix', 'g@teste.com', 'senha123'))

    def test_sem_pix_nao_mostra_qr(self):
        resposta = self.client.get(reverse('financeiro:configuracao'))
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn('pix_teste', resposta.context)

    def test_com_pix_mostra_qr_de_um_centavo(self):
        config = ConfiguracaoLocadora.obter()
        config.pix_tipo_chave = 'cpf'
        config.pix_chave = '12345678909'
        config.pix_nome_recebedor = 'Aluga Facil'
        config.pix_cidade_recebedor = 'Sao Paulo'
        config.save()

        resposta = self.client.get(reverse('financeiro:configuracao'))

        self.assertIn('54040.01', resposta.context['pix_teste'])
        self.assertContains(resposta, '<svg')


class MenuConfiguracaoTests(TestCase):

    def entrar_com_grupo(self, grupo):
        from django.contrib.auth.models import Group, User
        usuario = User.objects.create_user(f'usuario_{grupo}', password='senha123')
        usuario.groups.add(Group.objects.get_or_create(name=grupo)[0])
        self.client.force_login(usuario)

    def test_administrador_ve_configuracoes_no_menu(self):
        self.entrar_com_grupo('admin_locadora')
        resposta = self.client.get(reverse('core:dashboard'))
        self.assertContains(resposta, reverse('financeiro:configuracao'))

    def test_atendente_nao_ve_configuracoes_no_menu(self):
        self.entrar_com_grupo('atendente')
        resposta = self.client.get(reverse('core:dashboard'))
        self.assertNotContains(resposta, reverse('financeiro:configuracao'))


class CobrancaPublicaTests(FinanceiroTestBase):

    def setUp(self):
        super().setUp()
        self.cliente.nome = 'Carlos Alberto Souza'
        self.cliente.cpf = '529.982.247-25'
        self.cliente.save()
        config = ConfiguracaoLocadora.obter()
        config.pix_tipo_chave = 'cpf'
        config.pix_chave = '12345678909'
        config.pix_nome_recebedor = 'Aluga Facil'
        config.pix_cidade_recebedor = 'Sao Paulo'
        config.whatsapp_locadora = '(11) 98765-4321'
        config.save()
        self.contrato = self.criar_contrato()

    def criar_parcela(self, **kwargs):
        dados = dict(
            contrato=self.contrato, numero=2, valor=Decimal('700.00'),
            data_vencimento=timezone.localdate() + timedelta(days=2),
        )
        dados.update(kwargs)
        return ParcelaContrato.objects.create(**dados)

    def abrir(self, parcela):
        return self.client.get(parcela.caminho_cobranca)

    def test_abre_sem_login_com_valor_qr_e_copia_e_cola(self):
        parcela = self.criar_parcela()

        resposta = self.abrir(parcela)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'R$ 700,00')
        self.assertContains(resposta, '<svg')
        self.assertContains(resposta, parcela.pix_copia_e_cola())

    def test_mostra_so_o_primeiro_nome_e_nenhum_documento(self):
        resposta = self.abrir(self.criar_parcela())

        self.assertContains(resposta, 'Olá, Carlos')
        self.assertNotContains(resposta, 'Souza')
        self.assertNotContains(resposta, '529.982.247-25')

    def test_parcela_atrasada_mostra_valor_com_multa_e_juros(self):
        parcela = self.criar_parcela(
            situacao='em_atraso', data_vencimento=timezone.localdate() - timedelta(days=5)
        )

        resposta = self.abrir(parcela)

        self.assertContains(resposta, 'Inclui multa e juros')
        self.assertContains(resposta, f'{parcela.valor_corrigido:.2f}')

    def test_parcela_paga_nao_mostra_pix(self):
        parcela = self.criar_parcela(situacao='pago', data_pagamento=timezone.now())

        resposta = self.abrir(parcela)

        self.assertContains(resposta, 'Parcela quitada')
        self.assertNotContains(resposta, '<svg')
        self.assertNotContains(resposta, 'wa.me')

    def test_botao_de_comprovante_usa_whatsapp_da_locadora(self):
        resposta = self.abrir(self.criar_parcela())
        self.assertContains(resposta, 'https://wa.me/5511987654321?text=')

    def test_token_adulterado_e_recusado(self):
        parcela = self.criar_parcela()

        resposta = self.client.get(parcela.caminho_cobranca.replace('/cobranca/', '/cobranca/x'))

        self.assertEqual(resposta.status_code, 404)
        self.assertContains(resposta, 'não é válido', status_code=404)

    def test_token_expirado_e_recusado(self):
        parcela = self.criar_parcela()
        caminho = parcela.caminho_cobranca
        daqui_61_dias = time.time() + 61 * 24 * 3600

        with patch('django.core.signing.time.time', return_value=daqui_61_dias):
            resposta = self.client.get(caminho)

        self.assertEqual(resposta.status_code, 410)
        self.assertContains(resposta, 'expirou', status_code=410)

    def test_resposta_nao_fica_em_cache_nem_e_indexada(self):
        resposta = self.abrir(self.criar_parcela())

        self.assertEqual(resposta['Cache-Control'], 'no-store')
        self.assertIn('noindex', resposta['X-Robots-Tag'])

    def test_detalhe_do_contrato_oferece_link_da_parcela_em_aberto(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_link', 'l@teste.com', 'senha123'))
        self.criar_parcela()

        resposta = self.client.get(reverse('contratos:detalhe', args=[self.contrato.pk]))

        self.assertContains(resposta, "copiarLinkCobranca('/cobranca/")

    def test_agenda_oferece_link_pix_em_todas_as_secoes(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_agenda', 'a@teste.com', 'senha123'))
        hoje = timezone.localdate()
        self.criar_parcela(numero=1, situacao='em_atraso', data_vencimento=hoje - timedelta(days=3))
        self.criar_parcela(numero=2, data_vencimento=hoje + timedelta(days=2))
        self.criar_parcela(numero=3, data_vencimento=hoje + timedelta(days=20))

        resposta = self.client.get(reverse('financeiro:agenda'))

        self.assertContains(resposta, "copiarLinkCobranca('/cobranca/", count=3)
        self.assertContains(resposta, 'title="Abrir página do Pix (QR Code)"', count=3)

    def test_agenda_mostra_valor_com_multa_e_juros_da_parcela_atrasada(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_valor', 'v@teste.com', 'senha123'))
        parcela = self.criar_parcela(
            situacao='em_atraso', data_vencimento=timezone.localdate() - timedelta(days=5)
        )

        resposta = self.client.get(reverse('financeiro:agenda'))

        corrigido = f'{parcela.valor_corrigido:.2f}'.replace('.', ',')
        self.assertContains(resposta, f'R$ {corrigido}')
        self.assertContains(resposta, 'original R$ 700,00')
        self.assertEqual(resposta.context['total_atraso'], parcela.valor_corrigido)


class ReguaCobrancaTests(FinanceiroTestBase):

    def setUp(self):
        super().setUp()
        self.hoje = timezone.localdate()
        self.cliente.nome = 'Carlos Alberto Souza'
        self.cliente.celular = '(11) 98765-4321'
        self.cliente.save()
        config = ConfiguracaoLocadora.obter()
        config.pix_tipo_chave = 'cpf'
        config.pix_chave = '12345678909'
        config.pix_nome_recebedor = 'Aluga Facil'
        config.pix_cidade_recebedor = 'Sao Paulo'
        config.save()
        self.contrato = self.criar_contrato()

    def criar_parcela(self, dias_desde_vencimento, **kwargs):
        dados = dict(
            contrato=self.contrato, numero=2, valor=Decimal('700.00'),
            data_vencimento=self.hoje - timedelta(days=dias_desde_vencimento),
        )
        dados.update(kwargs)
        return ParcelaContrato.objects.create(**dados)

    def entrar(self):
        from django.contrib.auth.models import User
        usuario = User.objects.create_superuser('gestor_regua', 'r@teste.com', 'senha123')
        self.client.force_login(usuario)
        return usuario

    def test_etapa_do_dia_segue_a_regua(self):
        esperado = {
            -3: None, -2: None, -1: 'd_menos_1', 0: 'd0', 1: 'd_mais_1', 2: 'd_mais_1',
            3: 'd_mais_3', 6: 'd_mais_3', 7: 'd_mais_7', 30: 'd_mais_7',
        }
        for dias, etapa in esperado.items():
            with self.subTest(dias=dias):
                vencimento = self.hoje - timedelta(days=dias)
                self.assertEqual(NotificacaoCobranca.etapa_do_dia(vencimento, self.hoje), etapa)

    def test_etapa_ja_enviada_em_outro_dia_sai_da_lista(self):
        parcela = self.criar_parcela(10)
        NotificacaoCobranca.objects.create(
            parcela=parcela, canal='whatsapp', etapa='d_mais_7', valor=parcela.valor,
            enviado_em=timezone.now() - timedelta(days=3),
        )

        self.assertEqual(NotificacaoCobranca.a_contatar('whatsapp', [parcela]), [])

    def test_etapas_puladas_nao_sao_cobradas(self):
        parcela = self.criar_parcela(4)

        itens = NotificacaoCobranca.a_contatar('whatsapp', [parcela])

        self.assertEqual([(p, etapa) for p, etapa, _ in itens], [(parcela, 'd_mais_3')])

    def test_enviada_hoje_continua_na_lista_marcada(self):
        parcela = self.criar_parcela(0)
        enviada = NotificacaoCobranca.objects.create(
            parcela=parcela, canal='whatsapp', etapa='d0', valor=parcela.valor,
        )

        itens = NotificacaoCobranca.a_contatar('whatsapp', [parcela])

        self.assertEqual(itens, [(parcela, 'd0', enviada)])

    def test_parcela_paga_ou_longe_do_vencimento_fica_fora(self):
        paga = self.criar_parcela(1, numero=3, situacao='pago', data_pagamento=timezone.now())
        futura = self.criar_parcela(-5, numero=4)

        self.assertEqual(NotificacaoCobranca.a_contatar('whatsapp', [paga, futura]), [])

    def test_mensagem_de_atraso_traz_valor_corrigido_link_e_pix(self):
        parcela = self.criar_parcela(3, situacao='em_atraso')
        pix = parcela.pix_copia_e_cola()

        texto = NotificacaoCobranca.mensagem(parcela, 'd_mais_3', 'https://x/cobranca/abc/', pix)

        self.assertIn('Olá, Carlos.', texto)
        self.assertIn('ABC1D23', texto)
        self.assertIn('em atraso há 3 dias', texto)
        corrigido = f'{parcela.valor_corrigido:.2f}'.replace('.', ',')
        self.assertIn(f'multa e juros: R$ {corrigido}', texto)
        self.assertIn('https://x/cobranca/abc/', texto)
        self.assertIn(pix, texto)
        self.assertNotIn('Souza', texto)

    def test_mensagem_sem_encargo_nao_fala_em_multa(self):
        config = ConfiguracaoLocadora.obter()
        config.dias_carencia = 5
        config.save()
        parcela = self.criar_parcela(1)

        texto = NotificacaoCobranca.mensagem(parcela, 'd_mais_1', 'https://x/', None)

        self.assertIn('venceu ontem', texto)
        self.assertIn('Valor: R$ 700,00', texto)
        self.assertNotIn('multa', texto)
        self.assertNotIn('copia e cola', texto)

    def test_agenda_mostra_botao_do_whatsapp_com_mensagem_pronta(self):
        self.entrar()
        parcela = self.criar_parcela(-1)

        resposta = self.client.get(reverse('financeiro:agenda'))

        self.assertContains(resposta, 'A contatar hoje')
        self.assertContains(resposta, 'Vence amanhã')
        self.assertContains(resposta, 'https://wa.me/5511987654321?text=Ol%C3%A1%2C%20Carlos')
        self.assertContains(resposta, reverse('financeiro:cobrar-whatsapp', args=[parcela.pk]))

    def test_cliente_sem_celular_valido_mostra_corrigir_cadastro(self):
        self.entrar()
        self.cliente.celular = '(11) 3333-4444'
        self.cliente.save()
        self.criar_parcela(0)

        resposta = self.client.get(reverse('financeiro:agenda'))

        self.assertContains(resposta, 'Corrigir cadastro')
        self.assertContains(resposta, reverse('clientes:editar', args=[self.cliente.pk]))
        self.assertNotContains(resposta, 'https://wa.me/')

    def test_registrar_cobranca_grava_uma_vez_por_etapa(self):
        usuario = self.entrar()
        parcela = self.criar_parcela(3, situacao='em_atraso')
        url = reverse('financeiro:cobrar-whatsapp', args=[parcela.pk])

        primeira = self.client.post(url)
        self.client.post(url)

        self.assertEqual(primeira.status_code, 200)
        notificacao = NotificacaoCobranca.objects.get()
        self.assertEqual(notificacao.etapa, 'd_mais_3')
        self.assertEqual(notificacao.destino, '5511987654321')
        self.assertEqual(notificacao.valor, parcela.valor_corrigido)
        self.assertEqual(notificacao.enviado_por, usuario)

    def test_registrar_cobranca_recusa_parcela_fora_da_regua(self):
        self.entrar()
        futura = self.criar_parcela(-5)
        paga = self.criar_parcela(1, numero=3, situacao='pago', data_pagamento=timezone.now())

        fora = self.client.post(reverse('financeiro:cobrar-whatsapp', args=[futura.pk]))
        quitada = self.client.post(reverse('financeiro:cobrar-whatsapp', args=[paga.pk]))

        self.assertEqual(fora.status_code, 400)
        self.assertEqual(quitada.status_code, 404)
        self.assertFalse(NotificacaoCobranca.objects.exists())

    def test_atendente_nao_registra_cobranca(self):
        from django.contrib.auth.models import Group, User
        atendente = User.objects.create_user('atendente_regua', password='senha123')
        atendente.groups.add(Group.objects.get_or_create(name='atendente')[0])
        self.client.force_login(atendente)
        parcela = self.criar_parcela(0)

        self.client.post(reverse('financeiro:cobrar-whatsapp', args=[parcela.pk]))

        self.assertFalse(NotificacaoCobranca.objects.exists())

    def test_detalhe_do_contrato_mostra_historico_de_cobrancas(self):
        usuario = self.entrar()
        parcela = self.criar_parcela(1)
        NotificacaoCobranca.objects.create(
            parcela=parcela, canal='whatsapp', etapa='d_mais_1',
            valor=Decimal('714.00'), enviado_por=usuario,
        )

        resposta = self.client.get(reverse('contratos:detalhe', args=[self.contrato.pk]))

        self.assertContains(resposta, 'Cobranças Enviadas')
        self.assertContains(resposta, '1 dia de atraso')
        self.assertContains(resposta, 'R$ 714,00')


class AtrasoPorDataTests(FinanceiroTestBase):
    """Parcela vencida conta como atrasada mesmo sem a tarefa diária ter marcado em_atraso."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_atraso', 'at@teste.com', 'senha123'))
        self.hoje = timezone.localdate()
        self.contrato = self.criar_contrato(situacao='ativo', data_saida=timezone.now())

    def criar_parcela(self, dias_para_vencer, **kwargs):
        dados = dict(
            contrato=self.contrato, numero=2, valor=Decimal('700.00'),
            data_vencimento=self.hoje + timedelta(days=dias_para_vencer),
        )
        dados.update(kwargs)
        return ParcelaContrato.objects.create(**dados)

    def test_agenda_poe_parcela_vencida_pendente_em_atraso(self):
        vencida = self.criar_parcela(-3)
        a_vencer = self.criar_parcela(2, numero=3)

        resposta = self.client.get(reverse('financeiro:agenda'))

        self.assertEqual(resposta.context['em_atraso'], [vencida])
        self.assertEqual(resposta.context['esta_semana'], [a_vencer])
        self.assertEqual(resposta.context['total_atraso'], vencida.valor_corrigido)

    def test_dashboard_conta_parcela_vencida_pendente(self):
        self.criar_parcela(-3)
        self.criar_parcela(-1, numero=3, situacao='em_atraso')
        self.criar_parcela(2, numero=4)

        resposta = self.client.get(reverse('core:dashboard'))

        self.assertEqual(resposta.context['parcelas_atraso'], 2)

    def test_detalhe_do_contrato_marca_parcela_vencida_pendente(self):
        self.criar_parcela(-3)

        resposta = self.client.get(reverse('contratos:detalhe', args=[self.contrato.pk]))

        self.assertContains(resposta, '<span class="badge bg-danger">Em Atraso</span>', html=True)

    def test_checkin_marca_parcela_vencida_pendente(self):
        self.criar_parcela(-3)

        resposta = self.client.get(reverse('contratos:checkin', args=[self.contrato.pk]))

        self.assertContains(resposta, 'Em atraso')

    def test_fluxo_marca_parcela_de_despesa_vencida_pendente(self):
        despesa = DespesaOperacional.objects.create(
            categoria='seguro', descricao='Seguro frota', valor=Decimal('300.00'),
            data_competencia=date(2026, 10, 1), parcelado=True, numero_parcelas=1,
        )
        ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('300.00'), data_vencimento=date(2026, 11, 10),
        )

        with patch('django.utils.timezone.localdate', return_value=date(2026, 11, 15)):
            resposta = self.client.get(reverse('financeiro:fluxo'), {'ano': 2026, 'mes': 11})

        item = next(i for i in resposta.context['saidas_detalhadas'] if i['despesa_pk'] == despesa.pk)
        self.assertEqual(item['situacao'], 'em_atraso')


class ContasReceberParcelasAtrasadasTests(FinanceiroTestBase):
    """O card "Vencido" e a coluna de parcelas atrasadas olham as parcelas, não só o fim do contrato."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_cr', 'cr@teste.com', 'senha123'))
        self.hoje = timezone.localdate()
        self.contrato = self.criar_contrato(situacao='ativo', data_saida=timezone.now())
        self.conta = ContaReceber.objects.get(contrato=self.contrato)

    def criar_parcela(self, numero, dias_para_vencer, **kwargs):
        dados = dict(
            contrato=self.contrato, numero=numero, valor=Decimal('700.00'),
            data_vencimento=self.hoje + timedelta(days=dias_para_vencer),
        )
        dados.update(kwargs)
        return ParcelaContrato.objects.create(**dados)

    def test_contrato_em_andamento_com_parcelas_atrasadas_entra_no_vencido(self):
        self.criar_parcela(1, -10)
        self.criar_parcela(2, -3, situacao='em_atraso')
        self.criar_parcela(3, -1, situacao='pago')
        self.criar_parcela(4, 4)
        self.criar_parcela(5, -5, tipo='caucao')

        resposta = self.client.get(reverse('financeiro:contas-receber'))

        conta = resposta.context['contas'][0]
        self.assertEqual(conta.qtd_parcelas_atrasadas, 2)
        self.assertEqual(conta.valor_parcelas_atrasadas, Decimal('1400.00'))
        self.assertEqual(resposta.context['kpi_vencido'], Decimal('1400.00'))
        self.assertEqual(resposta.context['qtd_contas_em_atraso'], 1)
        self.assertContains(resposta, 'Em atraso')

    def test_contrato_encerrado_conta_o_saldo_inteiro(self):
        for numero in range(1, 5):
            self.criar_parcela(numero, -30 + 7 * numero, situacao='pago' if numero == 1 else 'pendente')
        PagamentoContrato.objects.create(
            contrato=self.contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('2000.00'),
        )
        ContaReceber.sincronizar(self.contrato.pk, novo_vencimento=self.hoje - timedelta(days=2))

        resposta = self.client.get(reverse('financeiro:contas-receber'))

        self.assertEqual(resposta.context['kpi_vencido'], Decimal('800.00'))

    def test_contrato_sem_atraso_nao_soma_no_vencido(self):
        self.criar_parcela(1, 3)

        resposta = self.client.get(reverse('financeiro:contas-receber'))

        self.assertEqual(resposta.context['kpi_vencido'], Decimal('0'))
        self.assertFalse(resposta.context['contas'][0].tem_atraso)


class VencidoIgualEmTodasAsTelasTests(FinanceiroTestBase):
    """Contrato em andamento com parcela atrasada aparece como vencido nas 4 telas."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_venc', 'v@teste.com', 'senha123'))
        hoje = timezone.localdate()
        self.contrato = self.criar_contrato(
            situacao='ativo', data_saida=timezone.now() - timedelta(days=10),
            data_devolucao_prevista=timezone.now() + timedelta(days=30),
        )
        ParcelaContrato.objects.create(
            contrato=self.contrato, numero=1, valor=Decimal('700.00'),
            data_vencimento=hoje - timedelta(days=10),
        )
        ParcelaContrato.objects.create(
            contrato=self.contrato, numero=2, valor=Decimal('700.00'),
            data_vencimento=hoje + timedelta(days=4),
        )

    def test_mesmo_valor_vencido_em_contas_a_receber_painel_e_inadimplencia(self):
        contas = self.client.get(reverse('financeiro:contas-receber')).context['kpi_vencido']
        painel = self.client.get(reverse('financeiro:index')).context['total_vencido']
        relatorio = self.client.get(reverse('core:relatorio-inadimplencia')).context

        self.assertEqual(contas, Decimal('700.00'))
        self.assertEqual(painel, Decimal('700.00'))
        self.assertEqual(relatorio['total_vencido'], Decimal('700.00'))
        self.assertEqual(relatorio['total_a_vencer'], Decimal('700.00'))
        self.assertEqual(relatorio['vencidas'][0].dias_atraso_efetivo, 10)


class ContasAPagarCompletoTests(FinanceiroTestBase):
    """Despesa avulsa não paga e repasse a investidor entram nas contas a pagar,
    e o Fluxo de Caixa só tira do saldo o que foi pago."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        from apps.investidores.models import CobrancaGestao, Investidor, VeiculoInvestidor
        self.client.force_login(User.objects.create_superuser('gestor_pagar', 'p@teste.com', 'senha123'))
        self.hoje = timezone.localdate()
        self.avulsa = DespesaOperacional.objects.create(
            categoria='manutencao', descricao='OS #1', valor=Decimal('450.00'),
            data_competencia=self.hoje - timedelta(days=2),
        )
        vinculo = VeiculoInvestidor.objects.create(
            investidor=Investidor.objects.create(nome='Investidor Teste'),
            veiculo=self.veiculo, taxa_gestao_semanal=Decimal('300.00'),
            data_inicio=self.hoje - timedelta(days=60),
        )
        self.repasse_pendente = CobrancaGestao.objects.create(
            veiculo_investidor=vinculo, semana_inicio=self.hoje - timedelta(days=13),
            semana_fim=self.hoje - timedelta(days=7), valor=Decimal('300.00'),
            data_vencimento=self.hoje - timedelta(days=1),
        )
        self.repasse_pago = CobrancaGestao.objects.create(
            veiculo_investidor=vinculo, semana_inicio=self.hoje - timedelta(days=20),
            semana_fim=self.hoje - timedelta(days=14), valor=Decimal('300.00'),
            data_vencimento=self.hoje, situacao='pago', data_pagamento=self.hoje,
        )

    def test_contas_a_pagar_inclui_avulsa_e_repasse(self):
        resposta = self.client.get(reverse('financeiro:contas-pagar'))

        self.assertEqual(resposta.context['kpi_total_pendente'], Decimal('750.00'))
        self.assertEqual(resposta.context['kpi_em_atraso'], Decimal('750.00'))
        self.assertIn(self.avulsa, resposta.context['avulsas_pendentes'])
        self.assertIn(self.repasse_pendente, resposta.context['repasses_pendentes'])

    def test_fluxo_de_caixa_so_desconta_o_que_foi_pago(self):
        DespesaOperacional.objects.filter(pk=self.avulsa.pk).update(data_competencia=self.hoje)
        self.repasse_pendente.data_vencimento = self.hoje
        self.repasse_pendente.save()

        resposta = self.client.get(
            reverse('financeiro:fluxo'), {'ano': self.hoje.year, 'mes': self.hoje.month},
        )

        self.assertEqual(resposta.context['total_saidas'], Decimal('300.00'))
        self.assertEqual(resposta.context['total_a_pagar'], Decimal('750.00'))

    def test_dre_inclui_so_os_repasses_pagos(self):
        resposta = self.client.get(
            reverse('core:relatorio-dre'), {'ano': self.hoje.year, 'mes': ''},
        )

        categorias = {d['categoria']: d['total'] for d in resposta.context['despesa_por_cat']}
        self.assertEqual(categorias['repasse_investidor'], Decimal('300.00'))


class OcupacaoPorSobreposicaoTests(FinanceiroTestBase):
    """Dias locados = sobreposição da locação com o período, até hoje."""

    def test_contrato_ativo_iniciado_no_mes_anterior_conta_os_dias_do_mes(self):
        from apps.contracts.models import dias_locados_por_veiculo
        hoje = date(2026, 11, 10)
        saida = timezone.make_aware(timezone.datetime(2026, 10, 20, 9, 0))
        self.criar_contrato(
            situacao='ativo', data_saida=saida,
            data_devolucao_prevista=saida + timedelta(days=60),
        )

        dias, periodo = dias_locados_por_veiculo(date(2026, 11, 1), date(2026, 11, 30), hoje=hoje)

        self.assertEqual(periodo, 10)
        self.assertEqual(dias[self.veiculo.pk], 10)

    def test_contrato_encerrado_conta_so_ate_a_devolucao(self):
        from apps.contracts.models import dias_locados_por_veiculo
        saida = timezone.make_aware(timezone.datetime(2026, 9, 3, 9, 0))
        self.criar_contrato(
            situacao='encerrado', data_saida=saida,
            data_devolucao_prevista=saida + timedelta(days=7),
            data_devolucao_real=saida + timedelta(days=5),
        )

        dias, periodo = dias_locados_por_veiculo(
            date(2026, 9, 1), date(2026, 9, 30), hoje=date(2026, 11, 4),
        )

        self.assertEqual(periodo, 30)
        self.assertEqual(dias[self.veiculo.pk], 5)


class DebitoAutomaticoDataPagamentoTests(FinanceiroTestBase):

    def criar_despesa(self):
        return DespesaOperacional.objects.create(
            categoria='seguro', descricao='Seguro parcelado', valor=Decimal('1200.00'),
            data_competencia=timezone.localdate() - timedelta(days=120),
            parcelado=True, numero_parcelas=12, forma_pagamento='cartao_credito', debito_automatico=True,
        )

    def test_baixa_automatica_grava_o_vencimento_e_a_forma_da_despesa(self):
        despesa = self.criar_despesa()
        vencimento = timezone.localdate() - timedelta(days=90)
        parcela = ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('100.00'), data_vencimento=vencimento, situacao='em_atraso',
        )

        DespesaOperacional.sincronizar_auto_pagamento()

        parcela.refresh_from_db()
        self.assertEqual(parcela.situacao, 'pago')
        self.assertEqual(parcela.data_pagamento, vencimento)
        self.assertEqual(parcela.forma_pagamento, 'cartao_credito')

    def test_comando_so_corrige_pagas_depois_do_vencimento_e_so_com_aplicar(self):
        from io import StringIO
        from django.core.management import call_command
        despesa = self.criar_despesa()
        hoje = timezone.localdate()
        errada = ParcelaDespesa.objects.create(
            despesa=despesa, numero=1, valor=Decimal('100.00'), situacao='pago',
            data_vencimento=hoje - timedelta(days=60), data_pagamento=hoje,
        )
        adiantada = ParcelaDespesa.objects.create(
            despesa=despesa, numero=2, valor=Decimal('100.00'), situacao='pago',
            data_vencimento=hoje + timedelta(days=30), data_pagamento=hoje, forma_pagamento='pix',
        )

        call_command('corrigir_data_debito_automatico', stdout=StringIO())
        errada.refresh_from_db()
        self.assertEqual(errada.data_pagamento, hoje)

        call_command('corrigir_data_debito_automatico', '--aplicar', stdout=StringIO())
        errada.refresh_from_db()
        adiantada.refresh_from_db()
        self.assertEqual(errada.data_pagamento, hoje - timedelta(days=60))
        self.assertEqual(errada.forma_pagamento, 'cartao_credito')
        self.assertEqual(adiantada.data_pagamento, hoje)
        self.assertEqual(adiantada.forma_pagamento, 'pix')


class ValoresMinimosFinanceiroTests(FinanceiroTestBase):

    def test_multa_com_valor_zero_e_recusada(self):
        from .forms import MultaTransitoForm
        form = MultaTransitoForm(data={
            'veiculo': self.veiculo.pk, 'data_infracao': timezone.localdate().isoformat(),
            'descricao': 'Velocidade', 'pontos': 4, 'valor': '0', 'situacao': 'pendente_identificacao',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('valor', form.errors)

    def test_despesa_com_valor_negativo_e_recusada(self):
        from .forms import DespesaOperacionalForm
        form = DespesaOperacionalForm(data={
            'categoria': 'outros', 'descricao': 'Teste', 'valor': '-10',
            'data_competencia': timezone.localdate().isoformat(),
        })
        self.assertFalse(form.is_valid())
        self.assertIn('valor', form.errors)

    def test_conferencia_aponta_valor_negativo_gravado(self):
        from io import StringIO
        from django.core.management import call_command
        from apps.contracts.models import AvariaContrato
        contrato = self.criar_contrato()
        AvariaContrato.objects.create(contrato=contrato, descricao='Risco', valor_cobrado=Decimal('-500.00'))
        saida = StringIO()
        call_command('conferir_integridade_financeira', stdout=saida)
        self.assertIn('avaria "Risco" com valor -500.00', saida.getvalue())


class MultaContratoPorDevolucaoRealTests(FinanceiroTestBase):

    def dados_form(self, data_infracao, **extra):
        dados = {
            'veiculo': self.veiculo.pk, 'data_infracao': data_infracao.isoformat(),
            'descricao': 'Velocidade', 'pontos': 4, 'valor': '195.23', 'situacao': 'pendente_identificacao',
        }
        dados.update(extra)
        return dados

    def criar_multa(self, dias_atras=2, **extra):
        return MultaTransito.objects.create(
            veiculo=self.veiculo, data_infracao=timezone.localdate() - timedelta(days=dias_atras),
            descricao='Velocidade', valor=Decimal('195.23'), **extra,
        )

    def test_liga_contrato_ativo_com_devolucao_prevista_vencida(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            situacao='ativo', data_saida=agora - timedelta(days=10), data_devolucao_prevista=agora - timedelta(days=6),
        )
        self.assertEqual(self.criar_multa().contrato_id, contrato.pk)

    def test_nao_liga_contrato_devolvido_antes_da_infracao(self):
        agora = timezone.now()
        self.criar_contrato(
            situacao='encerrado', data_saida=agora - timedelta(days=10),
            data_devolucao_prevista=agora + timedelta(days=5), data_devolucao_real=agora - timedelta(days=5),
        )
        self.assertIsNone(self.criar_multa().contrato_id)

    def test_contrato_em_fechamento_usa_a_devolucao_real(self):
        agora = timezone.now()
        contrato = self.criar_contrato(
            situacao='aguardando_devolucao', data_saida=agora - timedelta(days=10),
            data_devolucao_prevista=agora - timedelta(days=8), data_devolucao_real=agora - timedelta(days=1),
        )
        self.assertEqual(self.criar_multa().contrato_id, contrato.pk)

    def criar_troca_no_mesmo_dia(self):
        from datetime import datetime, time
        dia = timezone.localdate() - timedelta(days=3)

        def as_horas(hora):
            return timezone.make_aware(datetime.combine(dia, time(hora)))

        anterior = self.criar_contrato(
            situacao='encerrado', data_saida=as_horas(8) - timedelta(days=7),
            data_devolucao_prevista=as_horas(10), data_devolucao_real=as_horas(10),
        )
        seguinte = self.criar_contrato(
            situacao='ativo', data_saida=as_horas(14), data_devolucao_prevista=as_horas(14) + timedelta(days=7),
        )
        return dia, anterior, seguinte

    def test_troca_no_mesmo_dia_nao_liga_sozinha_e_o_form_pede_o_contrato(self):
        from .forms import MultaTransitoForm
        dia, anterior, seguinte = self.criar_troca_no_mesmo_dia()

        self.assertIsNone(self.criar_multa(dias_atras=3).contrato_id)

        form = MultaTransitoForm(data=self.dados_form(dia))
        self.assertFalse(form.is_valid())
        self.assertIn(anterior.numero, form.errors['contrato'][0])
        self.assertIn(seguinte.numero, form.errors['contrato'][0])

        form = MultaTransitoForm(data=self.dados_form(dia, contrato=seguinte.pk))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().contrato_id, seguinte.pk)

    def test_form_recusa_contrato_que_nao_estava_com_o_veiculo_no_dia(self):
        from .forms import MultaTransitoForm
        agora = timezone.now()
        outro = self.criar_contrato(
            situacao='encerrado', data_saida=agora - timedelta(days=30),
            data_devolucao_prevista=agora - timedelta(days=20), data_devolucao_real=agora - timedelta(days=20),
        )
        form = MultaTransitoForm(data=self.dados_form(timezone.localdate() - timedelta(days=2), contrato=outro.pk))
        self.assertFalse(form.is_valid())
        self.assertIn('contrato', form.errors)

    def test_multa_ja_cobrada_nao_troca_de_contrato(self):
        from .forms import MultaTransitoForm
        agora = timezone.now()
        self.criar_contrato(situacao='ativo', data_saida=agora - timedelta(days=5))
        multa = self.criar_multa(dias_atras=1, condutor_nome='Fulano', situacao='identificada')
        multa.situacao = 'cobrada_cliente'
        multa.save()
        multa.refresh_from_db()
        self.assertIsNotNone(multa.parcela_id)
        self.assertNotIn('contrato', MultaTransitoForm(instance=multa).fields)

    def test_comando_religa_multas_antigas_so_com_aplicar(self):
        from io import StringIO
        from django.core.management import call_command
        agora = timezone.now()
        contrato = self.criar_contrato(
            situacao='ativo', data_saida=agora - timedelta(days=10), data_devolucao_prevista=agora - timedelta(days=6),
        )
        multa = self.criar_multa()
        paga = self.criar_multa(situacao='paga')
        MultaTransito.objects.filter(pk__in=[multa.pk, paga.pk]).update(contrato=None)

        call_command('religar_multas_sem_contrato', stdout=StringIO())
        multa.refresh_from_db()
        self.assertIsNone(multa.contrato_id)

        call_command('religar_multas_sem_contrato', '--aplicar', stdout=StringIO())
        multa.refresh_from_db()
        paga.refresh_from_db()
        self.assertEqual(multa.contrato_id, contrato.pk)
        self.assertIsNone(paga.contrato_id)


class ResultadoRegimeDeCaixaTests(FinanceiroTestBase):

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_caixa', 'x@teste.com', 'senha123'))
        self.hoje = timezone.localdate()
        inicio_mes = self.hoje.replace(day=1)
        mes_passado = inicio_mes - timedelta(days=1)
        contrato = self.criar_contrato(situacao='ativo', data_saida=timezone.now())
        PagamentoContrato.objects.create(contrato=contrato, forma_pagamento='pix', tipo='locacao', valor=Decimal('1000.00'))
        PagamentoContrato.objects.create(contrato=contrato, forma_pagamento='pix', tipo='caucao', valor=Decimal('500.00'))
        DespesaOperacional.objects.create(
            categoria='manutencao', descricao='Competência passada, paga agora', valor=Decimal('200.00'),
            data_competencia=mes_passado, data_pagamento=self.hoje,
        )
        DespesaOperacional.objects.create(
            categoria='lavagem', descricao='Do mês, ainda não paga', valor=Decimal('80.00'),
            data_competencia=self.hoje,
        )
        DespesaOperacional.objects.create(
            categoria='caucao', descricao='Devolução de caução', valor=Decimal('500.00'),
            data_competencia=self.hoje, data_pagamento=self.hoje,
        )
        parcelada = DespesaOperacional.objects.create(
            categoria='seguro', descricao='Seguro', valor=Decimal('300.00'), parcelado=True, numero_parcelas=3,
            data_competencia=mes_passado,
        )
        ParcelaDespesa.objects.create(
            despesa=parcelada, numero=1, valor=Decimal('100.00'), situacao='pago',
            data_vencimento=mes_passado, data_pagamento=self.hoje,
        )
        ParcelaDespesa.objects.create(
            despesa=parcelada, numero=2, valor=Decimal('100.00'), situacao='pendente', data_vencimento=self.hoje,
        )

    def test_conta_so_o_que_entrou_e_foi_pago_no_mes_sem_caucao(self):
        from .resultado import resultado_caixa
        caixa = resultado_caixa(self.hoje.year, self.hoje.month)
        self.assertEqual(caixa['receita'], Decimal('1000.00'))
        self.assertEqual(caixa['despesa_por_categoria'], {'manutencao': Decimal('200.00'), 'seguro': Decimal('100.00')})
        self.assertEqual(caixa['resultado'], Decimal('700.00'))

    def test_dashboard_e_dre_mostram_o_mesmo_resultado_do_mes(self):
        periodo = {'ano': self.hoje.year, 'mes': self.hoje.month}
        dre = self.client.get(reverse('core:relatorio-dre'), periodo).context
        dashboard = self.client.get(reverse('core:dashboard')).context

        self.assertEqual(dre['resultado'], Decimal('700.00'))
        self.assertEqual(dashboard['resultado_mes'], dre['resultado'])
        self.assertEqual(dashboard['desp_pago'], dre['despesa_total'])
        import json
        self.assertEqual(json.loads(dre['chart_dre'])['resultados'][self.hoje.month - 1], 700.0)

    def test_fluxo_de_caixa_difere_da_dre_so_pelo_caucao(self):
        periodo = {'ano': self.hoje.year, 'mes': self.hoje.month}
        dre = self.client.get(reverse('core:relatorio-dre'), periodo).context
        fluxo = self.client.get(reverse('financeiro:fluxo'), periodo).context

        self.assertEqual(fluxo['total_entradas'] - Decimal('500.00'), dre['receita_total'])
        self.assertEqual(fluxo['total_saidas'] - Decimal('500.00'), dre['despesa_total'])
        self.assertEqual(fluxo['saldo'], dre['resultado'])


class ModuloInvestidoresDesligadoTests(FinanceiroTestBase):

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        from apps.investidores.models import CobrancaGestao, Investidor, VeiculoInvestidor
        self.client.force_login(User.objects.create_superuser('gestor_modulo', 'm@teste.com', 'senha123'))
        self.hoje = timezone.localdate()
        DespesaOperacional.objects.create(
            categoria='manutencao', descricao='OS #1', valor=Decimal('450.00'),
            data_competencia=self.hoje - timedelta(days=2),
        )
        vinculo = VeiculoInvestidor.objects.create(
            investidor=Investidor.objects.create(nome='Investidor Teste'),
            veiculo=self.veiculo, taxa_gestao_semanal=Decimal('300.00'),
            data_inicio=self.hoje - timedelta(days=60),
        )
        CobrancaGestao.objects.create(
            veiculo_investidor=vinculo, semana_inicio=self.hoje - timedelta(days=13),
            semana_fim=self.hoje - timedelta(days=7), valor=Decimal('300.00'),
            data_vencimento=self.hoje - timedelta(days=1),
        )
        CobrancaGestao.objects.create(
            veiculo_investidor=vinculo, semana_inicio=self.hoje - timedelta(days=20),
            semana_fim=self.hoje - timedelta(days=14), valor=Decimal('300.00'),
            data_vencimento=self.hoje, situacao='pago', data_pagamento=self.hoje,
        )

    def desligar(self):
        config = ConfiguracaoLocadora.obter()
        config.usar_modulo_investidores = False
        config.save()

    def test_ligado_por_padrao(self):
        self.assertTrue(ConfiguracaoLocadora.obter().usar_modulo_investidores)
        self.assertContains(self.client.get(reverse('core:dashboard')), 'Cobranças de Gestão')

    def test_desligado_tira_os_repasses_de_todos_os_numeros(self):
        self.desligar()
        periodo = {'ano': self.hoje.year, 'mes': self.hoje.month}

        dre = self.client.get(reverse('core:relatorio-dre'), {'ano': self.hoje.year, 'mes': ''}).context
        fluxo = self.client.get(reverse('financeiro:fluxo'), periodo).context
        pagar = self.client.get(reverse('financeiro:contas-pagar')).context
        dashboard = self.client.get(reverse('core:dashboard'))

        self.assertNotIn('repasse_investidor', {d['categoria'] for d in dre['despesa_por_cat']})
        self.assertFalse(any('Repasse' in s['descricao'] for s in fluxo['saidas_detalhadas']))
        self.assertEqual(pagar['repasses_pendentes'], [])
        self.assertEqual(pagar['kpi_total_pendente'], Decimal('450.00'))
        self.assertEqual(dashboard.context['desp_pago'], Decimal('0.00'))
        self.assertNotContains(dashboard, 'Cobranças de Gestão')

    def test_desligado_bloqueia_as_telas_do_modulo_e_religado_volta(self):
        self.desligar()
        self.assertEqual(self.client.get(reverse('investidores:lista')).status_code, 404)
        self.assertEqual(self.client.get(reverse('investidores:cobrancas')).status_code, 404)

        config = ConfiguracaoLocadora.obter()
        config.usar_modulo_investidores = True
        config.save()
        self.assertEqual(self.client.get(reverse('investidores:lista')).status_code, 200)
