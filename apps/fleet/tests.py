from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import CategoriaVeiculo, GrupoVeiculo, Veiculo
from .tarifas import conferir_tarifas, diaria_pela_semana, forma_cobranca_sugerida, resumo_cobranca, valor_base_ciclo


class RegraDeTarifaTests(SimpleTestCase):

    def test_periodo_completo_paga_o_pacote(self):
        self.assertEqual(valor_base_ciclo(Decimal('120'), 7, Decimal('750'), completo=True), Decimal('750.00'))

    def test_dias_soltos_pagam_diaria(self):
        self.assertEqual(valor_base_ciclo(Decimal('120'), 3, Decimal('750')), Decimal('360.00'))

    def test_dias_soltos_nunca_passam_do_pacote(self):
        self.assertEqual(valor_base_ciclo(Decimal('120'), 6, Decimal('600')), Decimal('600.00'))

    def test_sem_pacote_cobra_diaria_vezes_dias(self):
        self.assertEqual(valor_base_ciclo(Decimal('120'), 7, None, completo=True), Decimal('840.00'))

    def test_semanal_mais_caro_que_sete_diarias_e_erro_com_sugestao(self):
        resultado = conferir_tarifas(Decimal('100'), Decimal('800'))
        self.assertEqual(len(resultado['erros']), 1)
        self.assertEqual(resultado['erros'][0]['campo'], 'semanal')
        self.assertEqual(resultado['erros'][0]['sugestao'], Decimal('700.00'))

    def test_mensal_mais_caro_que_trinta_diarias_e_erro(self):
        resultado = conferir_tarifas(Decimal('100'), None, Decimal('3200'))
        self.assertEqual(resultado['erros'][0]['campo'], 'mensal')
        self.assertEqual(resultado['erros'][0]['sugestao'], Decimal('3000.00'))

    def test_mensal_mais_caro_que_trinta_dias_de_semanal_e_erro(self):
        resultado = conferir_tarifas(Decimal('120'), Decimal('700'), Decimal('3050'))
        self.assertEqual([e['campo'] for e in resultado['erros']], ['mensal'])
        self.assertEqual(resultado['erros'][0]['sugestao'], Decimal('3000.00'))

    def test_desconto_acima_de_50_porcento_e_aviso(self):
        resultado = conferir_tarifas(Decimal('100'), Decimal('300'))
        self.assertFalse(resultado['erros'])
        self.assertEqual(resultado['avisos'][0]['campo'], 'semanal')

    def test_valores_coerentes_nao_geram_erro_nem_aviso(self):
        resultado = conferir_tarifas(Decimal('120'), Decimal('750'), Decimal('2800'))
        self.assertEqual((resultado['erros'], resultado['avisos'], resultado['infos']), ([], [], []))

    def test_campos_vazios_informam_que_sera_pela_diaria(self):
        resultado = conferir_tarifas(Decimal('100'))
        self.assertEqual({i['campo'] for i in resultado['infos']}, {'semanal', 'mensal'})

    def test_resumo_mostra_exemplos(self):
        resumo = resumo_cobranca(Decimal('120'), Decimal('750'), Decimal('2800'))
        self.assertEqual(resumo['semana'], Decimal('750.00'))
        self.assertEqual(resumo['exemplo_semanal_10_dias'], Decimal('1110.00'))
        self.assertEqual(resumo['exemplo_mensal_45_dias'], Decimal('4600.00'))


class CadastroGrupoTarifaTests(TestCase):

    def setUp(self):
        self.usuario = User.objects.create_superuser('gestor_grupo', 'g@teste.com', 'senha123')
        self.client.force_login(self.usuario)
        self.categoria = CategoriaVeiculo.objects.create(nome='Econômico')

    def dados(self, **extra):
        base = {
            'nome': 'Entregadores', 'categoria': self.categoria.pk, 'descricao': '',
            'diaria': '100.00', 'semanal': '', 'mensal': '',
            'km_franquia_diaria': '300', 'valor_km_excedente': '1.00', 'caucao': '500.00', 'ativo': 'on',
        }
        base.update(extra)
        return base

    def test_bloqueia_semanal_mais_caro_que_sete_diarias(self):
        resposta = self.client.post(reverse('frota:grupo-novo'), self.dados(semanal='800.00'))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(GrupoVeiculo.objects.exists())
        self.assertContains(resposta, 'mais caro que 7 diárias')

    def test_desconto_alto_exige_confirmacao(self):
        resposta = self.client.post(reverse('frota:grupo-novo'), self.dados(semanal='300.00'))
        self.assertFalse(GrupoVeiculo.objects.exists())
        self.assertContains(resposta, 'Confirmo estes valores')

        resposta = self.client.post(
            reverse('frota:grupo-novo'), self.dados(semanal='300.00', confirmar_tarifas='on'), follow=True,
        )
        self.assertTrue(GrupoVeiculo.objects.filter(semanal=Decimal('300.00')).exists())

    def test_salvar_mostra_resumo_das_tarifas(self):
        resposta = self.client.post(
            reverse('frota:grupo-novo'), self.dados(semanal='650.00'), follow=True,
        )
        self.assertContains(resposta, 'semana R$ 650,00 (pacote)')
        self.assertContains(resposta, 'mês de 30 dias R$ 3.000,00 (pela diária)')

    def test_editar_tarifa_com_veiculos_avisa_que_contratos_antigos_nao_mudam(self):
        grupo = GrupoVeiculo.objects.create(nome='G', categoria=self.categoria, diaria=Decimal('100'))
        Veiculo.objects.create(
            placa='ABC1D23', marca='Fiat', modelo='Mobi', ano_fabricacao=2023, ano_modelo=2024,
            cor='Branco', grupo=grupo,
        )
        resposta = self.client.post(
            reverse('frota:grupo-editar', args=[grupo.pk]), self.dados(nome='G', diaria='110.00'), follow=True,
        )
        self.assertContains(resposta, '1 veículo(s) usam este grupo')

    def test_simulacao_ao_vivo_devolve_erro_e_sugestao(self):
        resposta = self.client.get(reverse('frota:grupo-simular-tarifas'), {'diaria': '100', 'semanal': '800'})
        dados = resposta.json()
        self.assertTrue(dados['pronto'])
        self.assertEqual(dados['erros'][0]['sugestao'], '700.00')
        self.assertEqual(resposta['Cache-Control'], 'no-store')

    def test_simulacao_sem_diaria_nao_quebra(self):
        dados = self.client.get(reverse('frota:grupo-simular-tarifas'), {'diaria': 'abc'}).json()
        self.assertFalse(dados['pronto'])

    def test_diaria_pela_semana_exata_e_arredondada_para_cima(self):
        self.assertEqual(diaria_pela_semana(Decimal('700')), Decimal('100.00'))
        self.assertEqual(diaria_pela_semana(Decimal('650')), Decimal('92.86'))
        self.assertEqual(diaria_pela_semana(Decimal('650.03')), Decimal('92.87'))
        for semanal in ('650', '650.03', '499.99', '1000.01'):
            self.assertGreaterEqual(diaria_pela_semana(Decimal(semanal)) * 7, Decimal(semanal))

    def test_salvar_so_com_semanal_calcula_a_diaria(self):
        self.client.post(reverse('frota:grupo-novo'), self.dados(diaria='', semanal='650.00'), follow=True)
        grupo = GrupoVeiculo.objects.get()
        self.assertEqual(grupo.diaria, Decimal('92.86'))
        self.assertEqual(grupo.semanal, Decimal('650.00'))

    def test_sem_diaria_e_sem_semanal_pede_um_dos_dois(self):
        resposta = self.client.post(reverse('frota:grupo-novo'), self.dados(diaria='', semanal=''))
        self.assertFalse(GrupoVeiculo.objects.exists())
        self.assertContains(resposta, 'Informe a diária ou o valor semanal.')

    def test_tela_do_grupo_tem_o_painel(self):
        resposta = self.client.get(reverse('frota:grupo-novo'))
        self.assertContains(resposta, 'Como este grupo vai cobrar')


class TarifasDoVeiculoTests(TestCase):

    def setUp(self):
        usuario = User.objects.create_superuser('gestor_tarifa', 't@teste.com', 'senha123')
        self.client.force_login(usuario)
        categoria = CategoriaVeiculo.objects.create(nome='Econômico')
        self.grupo = GrupoVeiculo.objects.create(
            nome='Hatch', categoria=categoria, diaria=Decimal('120'),
            semanal=Decimal('750'), mensal=Decimal('2800'),
        )

    def veiculo(self, grupo):
        return Veiculo.objects.create(
            placa='ABC1D23', marca='Fiat', modelo='Mobi', ano_fabricacao=2023, ano_modelo=2024,
            cor='Branco', grupo=grupo,
        )

    def test_devolve_pacotes_e_forma_sugerida(self):
        dados = self.client.get(reverse('frota:tarifas'), {'veiculo_id': self.veiculo(self.grupo).pk}).json()
        self.assertEqual(dados['semanal'], '750.00')
        self.assertEqual(dados['mensal'], '2800.00')
        self.assertEqual(dados['forma_cobranca'], 'semanal')

    def test_veiculo_inexistente_nao_quebra(self):
        resposta = self.client.get(reverse('frota:tarifas'), {'veiculo_id': 999999})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {})

    def test_forma_sugerida_mensal_so_quando_o_grupo_tem_apenas_mensal(self):
        self.grupo.semanal = None
        self.assertEqual(forma_cobranca_sugerida(self.grupo), 'mensal')
