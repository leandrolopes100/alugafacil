from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.contracts.models import Contrato
from apps.customers.models import Cliente
from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo

from .models import OrdemManutencao


class SituacaoDoVeiculoPelaOSTests(TestCase):
    """A situação do veículo segue as OS em andamento — nunca a OS agendada."""

    def setUp(self):
        categoria = CategoriaVeiculo.objects.create(nome='Economico')
        grupo = GrupoVeiculo.objects.create(nome='Grupo A', categoria=categoria, diaria=Decimal('100.00'))
        self.veiculo = Veiculo.objects.create(
            placa='OSA1B23', marca='Fiat', modelo='Argo', ano_fabricacao=2023,
            ano_modelo=2024, cor='Branco', grupo=grupo, km_atual=50000,
        )
        self.cliente = Cliente.objects.create(nome='Cliente OS')
        self.client.force_login(User.objects.create_superuser('gestor_os', 'os@teste.com', 'senha123'))

    def criar_os(self, situacao='agendada', **kwargs):
        dados = dict(
            veiculo=self.veiculo, tipo='preventiva', situacao=situacao,
            descricao='Troca de óleo', data_agendada=timezone.localdate() + timedelta(days=20),
        )
        dados.update(kwargs)
        return OrdemManutencao.objects.create(**dados)

    def alterar_status(self, ordem, situacao):
        return self.client.post(
            reverse('manutencao:alterar-status', args=[ordem.pk]), {'situacao': situacao},
        )

    def situacao_veiculo(self):
        self.veiculo.refresh_from_db()
        return self.veiculo.situacao

    def test_os_agendada_nao_tira_o_carro_de_circulacao(self):
        self.client.post(reverse('manutencao:nova'), {
            'veiculo': self.veiculo.pk, 'tipo': 'preventiva', 'situacao': 'agendada',
            'descricao': 'Revisão', 'data_agendada': (timezone.localdate() + timedelta(days=30)).isoformat(),
        })

        self.assertTrue(OrdemManutencao.objects.filter(veiculo=self.veiculo).exists())
        self.assertEqual(self.situacao_veiculo(), 'disponivel')

    def test_iniciar_os_coloca_o_carro_em_manutencao(self):
        ordem = self.criar_os()

        self.alterar_status(ordem, 'em_andamento')

        self.assertEqual(self.situacao_veiculo(), 'manutencao')

    def test_nao_inicia_os_com_carro_locado(self):
        Contrato.objects.create(
            cliente=self.cliente, veiculo=self.veiculo, diaria=Decimal('100.00'),
            situacao='ativo', data_saida=timezone.now(),
            data_devolucao_prevista=timezone.now() + timedelta(days=7),
        )
        Veiculo.objects.filter(pk=self.veiculo.pk).update(situacao='em_uso')
        ordem = self.criar_os()

        self.alterar_status(ordem, 'em_andamento')

        ordem.refresh_from_db()
        self.assertEqual(ordem.situacao, 'agendada')
        self.assertEqual(self.situacao_veiculo(), 'em_uso')

    def test_cancelar_uma_os_mantem_o_carro_preso_por_outra_em_andamento(self):
        primeira = self.criar_os()
        segunda = self.criar_os(descricao='Funilaria')
        self.alterar_status(primeira, 'em_andamento')
        self.alterar_status(segunda, 'em_andamento')

        self.alterar_status(segunda, 'cancelada')

        self.assertEqual(self.situacao_veiculo(), 'manutencao')

    def test_concluir_libera_o_carro_e_nunca_reduz_o_km(self):
        ordem = self.criar_os(km_na_manutencao=48000)
        self.alterar_status(ordem, 'em_andamento')

        self.alterar_status(ordem, 'concluida')

        self.veiculo.refresh_from_db()
        self.assertEqual(self.veiculo.situacao, 'disponivel')
        self.assertEqual(self.veiculo.km_atual, 50000)

    def test_concluir_com_km_maior_atualiza_o_veiculo(self):
        ordem = self.criar_os(km_na_manutencao=51200)
        self.alterar_status(ordem, 'em_andamento')

        self.alterar_status(ordem, 'concluida')

        self.veiculo.refresh_from_db()
        self.assertEqual(self.veiculo.km_atual, 51200)
