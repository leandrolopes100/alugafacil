"""
Management command para popular o banco de dados com dados de demonstração completos.

Cobre todos os módulos e cenários do sistema:
  - 10 veículos em todos os estados (disponível, em uso, reservado, manutenção, inativo)
  - 8 clientes (PF e PJ) com CNH
  - 8 contratos em todos os estados (aberto, ativo, aguardando_devolução, encerrado, cancelado)
  - Reservas, adicionais, avarias, pagamentos, parcelas
  - Contas a receber em todos os estados (pendente, pago_parcial, pago, vencido)
  - Despesas em todas as categorias
  - Multas de trânsito
  - Ordens de manutenção e alertas preventivos
  - Histórico de KM e documentos de veículos
  - Usuários com grupos de permissão
  - ConfiguracaoLocadora

Uso:
    python manage.py popular_dados_teste
    python manage.py popular_dados_teste --limpar
"""
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db.models.signals import post_save
from django.utils import timezone
from datetime import date, timedelta


class Command(BaseCommand):
    help = 'Popula o banco com dados de demonstração completos para o Aluga Fácil'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limpar',
            action='store_true',
            help='Apaga todos os dados existentes antes de inserir',
        )

    def handle(self, *args, **options):
        self._handle_within_schema(options)

    def _handle_within_schema(self, options):
        if options['limpar']:
            self._limpar_dados()

        # Desconecta signals para evitar criação duplicada de ContaReceber
        from apps.contracts.signals import contrato_post_save, pagamento_post_save
        from apps.contracts.models import Contrato, PagamentoContrato
        post_save.disconnect(contrato_post_save, sender=Contrato)
        post_save.disconnect(pagamento_post_save, sender=PagamentoContrato)

        try:
            self._criar_usuarios()
            self._criar_configuracao()
            self._criar_frota()
            self._criar_clientes()
            self._criar_reservas()
            self._criar_contratos()
            self._criar_financeiro()
            self._criar_manutencao()
        finally:
            post_save.connect(contrato_post_save, sender=Contrato)
            post_save.connect(pagamento_post_save, sender=PagamentoContrato)

        self.stdout.write(self.style.SUCCESS('\n  Dados de demonstração criados com sucesso!'))
        self._imprimir_resumo()

    # ─── Limpeza ──────────────────────────────────────────────────────────────

    def _limpar_dados(self):
        from apps.manutencao.models import OrdemManutencao, AlertaManutencao
        from apps.financeiro.models import ContaReceber, DespesaOperacional, MultaTransito
        from apps.contracts.models import (
            ParcelaContrato, PagamentoContrato, AvariaContrato,
            AdicionalContrato, FotoContrato, Contrato, Reserva,
        )
        from apps.customers.models import CNHCliente, Cliente
        from apps.fleet.models import (
            HistoricoKmVeiculo, DocumentoVeiculo, FotoVeiculo,
            Veiculo, GrupoVeiculo, CategoriaVeiculo,
        )

        AlertaManutencao.objects.all().delete()
        OrdemManutencao.objects.all().delete()
        ContaReceber.objects.all().delete()
        MultaTransito.objects.all().delete()
        DespesaOperacional.objects.all().delete()
        ParcelaContrato.objects.all().delete()
        PagamentoContrato.objects.all().delete()
        AvariaContrato.objects.all().delete()
        AdicionalContrato.objects.all().delete()
        FotoContrato.objects.all().delete()
        Contrato.objects.all().delete()
        Reserva.objects.all().delete()
        CNHCliente.objects.all().delete()
        Cliente.objects.all().delete()
        HistoricoKmVeiculo.objects.all().delete()
        DocumentoVeiculo.objects.all().delete()
        FotoVeiculo.objects.all().delete()
        Veiculo.objects.all().delete()
        GrupoVeiculo.objects.all().delete()
        CategoriaVeiculo.objects.all().delete()
        self.stdout.write(self.style.WARNING('  Dados anteriores removidos.'))

    # ─── Usuários ──────────────────────────────────────────────────────────────

    def _criar_usuarios(self):
        from django.contrib.auth.models import User, Group
        self.stdout.write('  Criando usuários...')

        # Garante que os grupos existam (criados pelo command criar_grupos)
        grupos = {}
        for nome in ('admin_locadora', 'atendente', 'financeiro', 'mecanico'):
            g, _ = Group.objects.get_or_create(name=nome)
            grupos[nome] = g

        # Superusuário admin
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@alugafacil.com', 'admin123')

        # Atendente
        if not User.objects.filter(username='atendente').exists():
            u = User.objects.create_user('atendente', 'atendente@alugafacil.com', 'senha123',
                                         first_name='Julia', last_name='Atendimento')
            u.groups.add(grupos['atendente'])

        # Financeiro
        if not User.objects.filter(username='financeiro').exists():
            u = User.objects.create_user('financeiro', 'fin@alugafacil.com', 'senha123',
                                         first_name='Roberto', last_name='Financeiro')
            u.groups.add(grupos['financeiro'])

        # Mecânico
        if not User.objects.filter(username='mecanico').exists():
            u = User.objects.create_user('mecanico', 'mec@alugafacil.com', 'senha123',
                                         first_name='Paulo', last_name='Mecânico')
            u.groups.add(grupos['mecanico'])

        self.admin_user = User.objects.get(username='admin')
        self.stdout.write(f'    {User.objects.count()} usuários')

    # ─── Configuração ─────────────────────────────────────────────────────────

    def _criar_configuracao(self):
        from apps.financeiro.models import ConfiguracaoLocadora
        ConfiguracaoLocadora.objects.get_or_create(
            pk=1,
            defaults=dict(
                percentual_multa_atraso=Decimal('2.00'),
                percentual_juros_diario=Decimal('0.0333'),
                dias_carencia=3,
            )
        )

    # ─── Frota ────────────────────────────────────────────────────────────────

    def _criar_frota(self):
        from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo, DocumentoVeiculo

        self.stdout.write('  Criando frota...')
        hoje = timezone.now().date()

        # Categorias
        cat_eco = CategoriaVeiculo.objects.create(
            nome='Econômico', icone='bi-car-front', cor='#3B82F6', ordem=1)
        cat_med = CategoriaVeiculo.objects.create(
            nome='Intermediário', icone='bi-car-front-fill', cor='#10B981', ordem=2)
        cat_suv = CategoriaVeiculo.objects.create(
            nome='SUV / Premium', icone='bi-truck', cor='#F59E0B', ordem=3)

        # Grupos
        self.g_hatch = GrupoVeiculo.objects.create(
            nome='Hatch Econômico', categoria=cat_eco,
            diaria=Decimal('120.00'), semanal=Decimal('750.00'), mensal=Decimal('2800.00'),
            caucao=Decimal('500.00'), km_franquia_diaria=200,
            valor_km_excedente=Decimal('1.50'),
            descricao='Veículos compactos com ótimo custo-benefício.',
        )
        self.g_sedan = GrupoVeiculo.objects.create(
            nome='Sedan Intermediário', categoria=cat_med,
            diaria=Decimal('160.00'), semanal=Decimal('1000.00'), mensal=Decimal('3800.00'),
            caucao=Decimal('800.00'), km_franquia_diaria=250,
            valor_km_excedente=Decimal('2.00'),
            descricao='Sedans confortáveis para viagens longas.',
        )
        self.g_suv = GrupoVeiculo.objects.create(
            nome='SUV Premium', categoria=cat_suv,
            diaria=Decimal('220.00'), semanal=Decimal('1400.00'), mensal=Decimal('5200.00'),
            caucao=Decimal('1200.00'), km_franquia_diaria=300,
            valor_km_excedente=Decimal('2.50'),
            descricao='SUVs e pickups premium para maior conforto e espaço.',
        )

        # ── Veículos ──────────────────────────────────────────────────────────
        self.v_gol = Veiculo.objects.create(
            placa='ABC1D23', marca='Volkswagen', modelo='Gol',
            grupo=self.g_hatch, ano_fabricacao=2022, ano_modelo=2023,
            cor='Branco Cristal', combustivel='flex', transmissao='manual',
            portas=4, lugares=5, km_atual=25100, situacao='disponivel',
            chassi='9BWZZZ377VT004251', renavam='01234567890',
            data_aquisicao=date(2022, 3, 10), valor_aquisicao=Decimal('62000.00'),
            valor_fipe=Decimal('58000.00'),
        )
        self.v_argo = Veiculo.objects.create(
            placa='JKL4G56', marca='Fiat', modelo='Argo Drive',
            grupo=self.g_hatch, ano_fabricacao=2022, ano_modelo=2022,
            cor='Vermelho Competizione', combustivel='flex', transmissao='manual',
            portas=4, lugares=5, km_atual=35000, situacao='reservado',
            chassi='9BD158A97P0123456', renavam='12345678901',
            data_aquisicao=date(2022, 8, 15), valor_aquisicao=Decimal('68000.00'),
        )
        self.v_onix = Veiculo.objects.create(
            placa='PQR7J89', marca='Chevrolet', modelo='Onix Plus',
            grupo=self.g_hatch, ano_fabricacao=2023, ano_modelo=2023,
            cor='Azul Topázio', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=8200, situacao='disponivel',
            data_aquisicao=date(2023, 5, 20), valor_aquisicao=Decimal('82000.00'),
            valor_fipe=Decimal('78000.00'),
        )
        self.v_corolla = Veiculo.objects.create(
            placa='DEF2E34', marca='Toyota', modelo='Corolla XEi',
            grupo=self.g_sedan, ano_fabricacao=2023, ano_modelo=2023,
            cor='Prata Metálico', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=18900, situacao='em_uso',
            chassi='9BFZE59J7P0012345', renavam='23456789012',
            data_aquisicao=date(2023, 1, 20), valor_aquisicao=Decimal('145000.00'),
            valor_fipe=Decimal('138000.00'),
        )
        self.v_civic = Veiculo.objects.create(
            placa='STU8K90', marca='Honda', modelo='Civic EXL',
            grupo=self.g_sedan, ano_fabricacao=2022, ano_modelo=2022,
            cor='Cinza Grafite', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=31500, situacao='em_uso',
            data_aquisicao=date(2022, 4, 12), valor_aquisicao=Decimal('128000.00'),
        )
        self.v_cruze = Veiculo.objects.create(
            placa='MNO5H67', marca='Chevrolet', modelo='Cruze LT',
            grupo=self.g_sedan, ano_fabricacao=2021, ano_modelo=2022,
            cor='Cinza Urbano', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=42000, situacao='manutencao',
            chassi='9BGJK69BLCB234567', renavam='34567890123',
            data_aquisicao=date(2021, 11, 3), valor_aquisicao=Decimal('118000.00'),
        )
        self.v_hrv = Veiculo.objects.create(
            placa='GHI3F45', marca='Honda', modelo='HR-V EXL',
            grupo=self.g_suv, ano_fabricacao=2023, ano_modelo=2024,
            cor='Preto Cristal', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=12000, situacao='disponivel',
            chassi='9HGFE3S58RU090348', renavam='45678901234',
            data_aquisicao=date(2023, 6, 5), valor_aquisicao=Decimal('168000.00'),
            valor_fipe=Decimal('160000.00'),
        )
        self.v_compass = Veiculo.objects.create(
            placa='VWX9L01', marca='Jeep', modelo='Compass Limited',
            grupo=self.g_suv, ano_fabricacao=2023, ano_modelo=2024,
            cor='Azul Ocean', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=5800, situacao='em_uso',
            data_aquisicao=date(2023, 9, 18), valor_aquisicao=Decimal('215000.00'),
            valor_fipe=Decimal('205000.00'),
        )
        self.v_yaris = Veiculo.objects.create(
            placa='YZA0M12', marca='Toyota', modelo='Yaris Hatch',
            grupo=self.g_hatch, ano_fabricacao=2021, ano_modelo=2021,
            cor='Branco Perolado', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=55000, situacao='disponivel',
            data_aquisicao=date(2021, 6, 30), valor_aquisicao=Decimal('78000.00'),
        )
        self.v_t_cross = Veiculo.objects.create(
            placa='BCD1N34', marca='Volkswagen', modelo='T-Cross Comfortline',
            grupo=self.g_suv, ano_fabricacao=2022, ano_modelo=2022,
            cor='Vermelho Sunset', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=47000, situacao='inativo',
            data_aquisicao=date(2022, 2, 14), valor_aquisicao=Decimal('132000.00'),
        )

        # Documentos de veículos
        docs = [
            (self.v_gol,    'crlv',    '2025000001', date(2026, 12, 31)),
            (self.v_gol,    'seguro',  'AP-00123456', date(2026, 8, 10)),
            (self.v_corolla,'crlv',    '2025000002', date(2026, 12, 31)),
            (self.v_corolla,'seguro',  'AP-00234567', date(2027, 1, 20)),
            (self.v_hrv,    'crlv',    '2025000003', date(2026, 12, 31)),
            (self.v_hrv,    'vistoria','VI-00001234', date(2026, 6, 5)),   # próximo de vencer
            (self.v_cruze,  'crlv',    '2025000004', date(2025, 12, 31)), # VENCIDO
            (self.v_argo,   'crlv',    '2025000005', date(2026, 12, 31)),
            (self.v_argo,   'extintor','EX-00099881', date(2026, 9, 15)),
            (self.v_compass,'crlv',    '2025000006', date(2026, 12, 31)),
            (self.v_compass,'seguro',  'AP-00345678', date(2027, 9, 18)),
        ]
        for veiculo, tipo, numero, validade in docs:
            DocumentoVeiculo.objects.create(
                veiculo=veiculo, tipo=tipo, numero=numero,
                data_validade=validade, dias_alerta=30,
            )

        self.stdout.write(
            f'    {CategoriaVeiculo.objects.count()} categorias, '
            f'{GrupoVeiculo.objects.count()} grupos, '
            f'{Veiculo.objects.count()} veículos'
        )

    # ─── Clientes ─────────────────────────────────────────────────────────────

    def _criar_clientes(self):
        from apps.customers.models import Cliente, CNHCliente

        self.stdout.write('  Criando clientes...')

        self.c_joao = Cliente.objects.create(
            tipo='pf', nome='João Carlos Silva',
            cpf='123.456.789-01', data_nascimento=date(1985, 3, 15),
            email='joao.silva@email.com', celular='(11) 98765-4321',
            logradouro='Rua das Flores', numero='123', bairro='Jardim Primavera',
            cidade='São Paulo', estado='SP', cep='01234-567', situacao='ativo',
        )
        CNHCliente.objects.create(
            cliente=self.c_joao, numero='01234567890', estado_emissor='SP',
            categoria='b', validade=date(2029, 3, 15), principal=True,
        )

        self.c_maria = Cliente.objects.create(
            tipo='pf', nome='Maria Santos Oliveira',
            cpf='234.567.890-12', data_nascimento=date(1990, 7, 22),
            email='maria.santos@email.com', celular='(11) 97654-3210',
            logradouro='Rua Vergueiro', numero='800', bairro='Liberdade',
            cidade='São Paulo', estado='SP', cep='04101-000', situacao='ativo',
        )
        CNHCliente.objects.create(
            cliente=self.c_maria, numero='12345678901', estado_emissor='SP',
            categoria='b', validade=date(2027, 7, 22), principal=True,
        )

        self.c_carlos = Cliente.objects.create(
            tipo='pf', nome='Carlos Eduardo Ferreira',
            cpf='345.678.901-23', data_nascimento=date(1978, 11, 8),
            email='carlos.ferreira@gmail.com', celular='(11) 96543-2109',
            logradouro='Av. Paulista', numero='1500', complemento='Apto 42',
            bairro='Bela Vista', cidade='São Paulo', estado='SP', cep='01310-100',
            situacao='ativo',
        )
        CNHCliente.objects.create(
            cliente=self.c_carlos, numero='34567890123', estado_emissor='SP',
            categoria='b', validade=date(2028, 11, 8), principal=True,
        )

        self.c_ana = Cliente.objects.create(
            tipo='pf', nome='Ana Paula Rodrigues',
            cpf='456.789.012-34', data_nascimento=date(1995, 4, 30),
            email='ana.rodrigues@email.com', celular='(11) 95432-1098',
            logradouro='Rua Ibirapuera', numero='255', bairro='Moema',
            cidade='São Paulo', estado='SP', cep='04029-000', situacao='ativo',
        )
        CNHCliente.objects.create(
            cliente=self.c_ana, numero='45678901234', estado_emissor='SP',
            categoria='b', validade=date(2030, 4, 30), principal=True,
        )

        self.c_pedro = Cliente.objects.create(
            tipo='pf', nome='Pedro Henrique Matos',
            cpf='567.890.123-45', data_nascimento=date(1982, 12, 3),
            email='pedro.matos@empresa.com', celular='(21) 98877-6655',
            logradouro='Av. Rio Branco', numero='45', bairro='Centro',
            cidade='Rio de Janeiro', estado='RJ', cep='20040-004', situacao='ativo',
        )
        CNHCliente.objects.create(
            cliente=self.c_pedro, numero='56789012345', estado_emissor='RJ',
            categoria='b', validade=date(2025, 6, 15),  # CNH VENCIDA — para teste
            principal=True,
        )

        self.c_lucia = Cliente.objects.create(
            tipo='pf', nome='Lúcia Fernanda Costa',
            cpf='678.901.234-56', data_nascimento=date(1970, 9, 18),
            email='lucia.costa@hotmail.com', celular='(11) 94321-0987',
            logradouro='Rua Augusta', numero='2000', bairro='Consolação',
            cidade='São Paulo', estado='SP', cep='01304-000', situacao='ativo',
        )
        CNHCliente.objects.create(
            cliente=self.c_lucia, numero='67890123456', estado_emissor='SP',
            categoria='b', validade=date(2031, 9, 18), principal=True,
        )

        # Clientes PJ
        self.c_alpha = Cliente.objects.create(
            tipo='pj', razao_social='Construtora Alpha LTDA',
            cnpj='12.345.678/0001-90', contato='Roberto Alves',
            email='financeiro@alphaconstrucao.com.br', telefone='(11) 3234-5678',
            logradouro='Av. Brigadeiro Faria Lima', numero='3500',
            bairro='Itaim Bibi', cidade='São Paulo', estado='SP', cep='04538-132',
            situacao='ativo',
        )

        self.c_logistica = Cliente.objects.create(
            tipo='pj', razao_social='Logística Express S/A',
            cnpj='98.765.432/0001-10', contato='Fernanda Lima',
            email='operacoes@logisticaexpress.com', telefone='(11) 3456-9870',
            logradouro='Rod. Anhanguera', numero='1200', bairro='Distrito Industrial',
            cidade='Campinas', estado='SP', cep='13032-200',
            situacao='ativo',
        )

        # Cliente bloqueado (para testar o comportamento de bloqueio)
        self.c_bloqueado = Cliente.objects.create(
            tipo='pf', nome='Marcos Andrade Pereira',
            cpf='789.012.345-67', data_nascimento=date(1975, 5, 20),
            email='marcos.pereira@email.com', celular='(11) 93210-8765',
            logradouro='Rua XV de Novembro', numero='300',
            bairro='Centro', cidade='São Paulo', estado='SP', cep='01013-001',
            situacao='bloqueado',
            motivo_bloqueio='Contrato anterior encerrado com avaria não quitada.',
        )

        self.stdout.write(
            f'    {Cliente.objects.count()} clientes, '
            f'{__import__("apps.customers.models", fromlist=["CNHCliente"]).CNHCliente.objects.count()} CNHs'
        )

    # ─── Reservas ─────────────────────────────────────────────────────────────

    def _criar_reservas(self):
        from apps.contracts.models import Reserva
        self.stdout.write('  Criando reservas...')

        # Reserva futura confirmada — Maria + Argo
        self.res_maria = Reserva.objects.create(
            cliente=self.c_maria, grupo_veiculo=self.g_hatch,
            veiculo=self.v_argo, situacao='confirmada', canal='whatsapp',
            data_retirada=timezone.now() + timedelta(days=3),
            data_devolucao=timezone.now() + timedelta(days=17),
            diaria_cotada=Decimal('120.00'), caucao_cotado=Decimal('500.00'),
        )

        # Reserva futura pendente — Alpha (sem veículo ainda)
        self.res_alpha = Reserva.objects.create(
            cliente=self.c_alpha, grupo_veiculo=self.g_suv,
            veiculo=None, situacao='pendente', canal='balcao',
            data_retirada=timezone.now() + timedelta(days=10),
            data_devolucao=timezone.now() + timedelta(days=24),
            diaria_cotada=Decimal('220.00'), caucao_cotado=Decimal('1200.00'),
        )

        # Reserva futura — Ana via site
        Reserva.objects.create(
            cliente=self.c_ana, grupo_veiculo=self.g_sedan,
            veiculo=None, situacao='pendente', canal='site',
            data_retirada=timezone.now() + timedelta(days=15),
            data_devolucao=timezone.now() + timedelta(days=22),
            diaria_cotada=Decimal('160.00'), caucao_cotado=Decimal('800.00'),
        )

        # Reserva no_show (passou da data, cliente não apareceu)
        Reserva.objects.create(
            cliente=self.c_pedro, grupo_veiculo=self.g_hatch,
            veiculo=self.v_yaris, situacao='no_show', canal='telefone',
            data_retirada=timezone.now() - timedelta(days=5),
            data_devolucao=timezone.now() + timedelta(days=2),
            diaria_cotada=Decimal('120.00'), caucao_cotado=Decimal('500.00'),
        )

        # Reserva cancelada — Logística
        Reserva.objects.create(
            cliente=self.c_logistica, grupo_veiculo=self.g_suv,
            veiculo=None, situacao='cancelada', canal='telefone',
            data_retirada=timezone.now() - timedelta(days=2),
            data_devolucao=timezone.now() + timedelta(days=5),
            diaria_cotada=Decimal('220.00'), caucao_cotado=Decimal('1200.00'),
        )

        self.stdout.write(f'    {Reserva.objects.count()} reservas')

    # ─── Contratos ────────────────────────────────────────────────────────────

    def _criar_contratos(self):
        from apps.contracts.models import (
            Reserva, Contrato, AdicionalContrato, AvariaContrato,
            PagamentoContrato, ParcelaContrato,
        )
        from apps.financeiro.models import ContaReceber
        from apps.fleet.models import HistoricoKmVeiculo

        self.stdout.write('  Criando contratos...')
        hoje = timezone.now().date()
        agora = timezone.now()

        # ── CONTRATO 1: ATIVO (Carlos + Corolla, iniciou há 7 dias) ──────────
        saida_1 = agora - timedelta(days=7)
        dev_prev_1 = agora + timedelta(days=21)

        res_1 = Reserva.objects.create(
            cliente=self.c_carlos, grupo_veiculo=self.g_sedan,
            veiculo=self.v_corolla, situacao='ativa', canal='balcao',
            data_retirada=saida_1, data_devolucao=dev_prev_1,
            diaria_cotada=Decimal('160.00'), caucao_cotado=Decimal('800.00'),
        )
        c1 = Contrato.objects.create(
            numero='AF-2026-0001', reserva=res_1,
            cliente=self.c_carlos, veiculo=self.v_corolla,
            situacao='ativo', criado_por=self.admin_user,
            data_saida=saida_1, km_saida=18000, combustivel_saida='cheio',
            data_devolucao_prevista=dev_prev_1,
            diaria=Decimal('160.00'), km_franquia_diaria=250,
            valor_km_excedente=Decimal('2.00'),
            caucao_valor=Decimal('800.00'), caucao_situacao='pago',
            caucao_pago_em=saida_1 + timedelta(minutes=30),
        )
        AdicionalContrato.objects.create(
            contrato=c1, tipo='seguro_basico',
            descricao='Seguro básico diário', diaria=Decimal('15.00'), quantidade=1,
        )
        # Parcelas
        ParcelaContrato.objects.create(
            contrato=c1, numero=1, tipo='caucao',
            data_vencimento=saida_1.date(), valor=Decimal('800.00'),
            situacao='pago', data_pagamento=saida_1 + timedelta(minutes=30),
            forma_pagamento='pix', origem='original',
        )
        ParcelaContrato.objects.create(
            contrato=c1, numero=2, tipo='semanal',
            data_vencimento=saida_1.date() + timedelta(days=7),
            valor=Decimal('1120.00'), situacao='pago',
            data_pagamento=agora - timedelta(hours=2),
            forma_pagamento='pix', origem='original',
        )
        ParcelaContrato.objects.create(
            contrato=c1, numero=3, tipo='semanal',
            data_vencimento=saida_1.date() + timedelta(days=14),
            valor=Decimal('1120.00'), situacao='pendente', origem='original',
        )
        ParcelaContrato.objects.create(
            contrato=c1, numero=4, tipo='semanal',
            data_vencimento=saida_1.date() + timedelta(days=21),
            valor=Decimal('1120.00'), situacao='pendente', origem='original',
        )
        PagamentoContrato.objects.create(
            contrato=c1, forma_pagamento='pix', tipo='caucao',
            valor=Decimal('800.00'), data_pagamento=saida_1 + timedelta(minutes=30),
        )
        PagamentoContrato.objects.create(
            contrato=c1, forma_pagamento='pix', tipo='locacao',
            valor=Decimal('1120.00'), data_pagamento=agora - timedelta(hours=2),
        )
        ContaReceber.objects.create(
            contrato=c1, cliente=self.c_carlos,
            descricao=f'Locação — {c1.numero}',
            valor_total=Decimal('4960.00'),  # 800 + 1120*3 + 15*28 (adicional)
            valor_pago=Decimal('1920.00'),
            data_emissao=saida_1.date(), data_vencimento=dev_prev_1.date(),
            situacao='pago_parcial',
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_corolla, km=18000, contrato=c1,
            data=saida_1, origem='contrato_saida', registrado_por=self.admin_user,
        )

        # ── CONTRATO 2: ATIVO (Compass + Logística, iniciou há 3 dias) ───────
        saida_2 = agora - timedelta(days=3)
        dev_prev_2 = agora + timedelta(days=11)

        res_2 = Reserva.objects.create(
            cliente=self.c_logistica, grupo_veiculo=self.g_suv,
            veiculo=self.v_compass, situacao='ativa', canal='telefone',
            data_retirada=saida_2, data_devolucao=dev_prev_2,
            diaria_cotada=Decimal('220.00'), caucao_cotado=Decimal('1200.00'),
        )
        c2 = Contrato.objects.create(
            numero='AF-2026-0002', reserva=res_2,
            cliente=self.c_logistica, veiculo=self.v_compass,
            situacao='ativo', criado_por=self.admin_user,
            data_saida=saida_2, km_saida=5600, combustivel_saida='cheio',
            data_devolucao_prevista=dev_prev_2,
            diaria=Decimal('220.00'), km_franquia_diaria=300,
            valor_km_excedente=Decimal('2.50'),
            caucao_valor=Decimal('1200.00'), caucao_situacao='pago',
            caucao_pago_em=saida_2 + timedelta(minutes=45),
        )
        AdicionalContrato.objects.create(
            contrato=c2, tipo='condutor_adicional',
            descricao='Condutor adicional habilitado', diaria=Decimal('20.00'), quantidade=1,
        )
        AdicionalContrato.objects.create(
            contrato=c2, tipo='seguro_completo',
            descricao='Seguro completo com franquia zero', diaria=Decimal('35.00'), quantidade=1,
        )
        ParcelaContrato.objects.create(
            contrato=c2, numero=1, tipo='caucao',
            data_vencimento=saida_2.date(), valor=Decimal('1200.00'),
            situacao='pago', data_pagamento=saida_2 + timedelta(minutes=45),
            forma_pagamento='cartao_credito', origem='original',
        )
        ParcelaContrato.objects.create(
            contrato=c2, numero=2, tipo='semanal',
            data_vencimento=saida_2.date() + timedelta(days=7),
            valor=Decimal('1540.00'), situacao='pendente', origem='original',
        )
        PagamentoContrato.objects.create(
            contrato=c2, forma_pagamento='cartao_credito', tipo='caucao',
            valor=Decimal('1200.00'), data_pagamento=saida_2 + timedelta(minutes=45),
        )
        ContaReceber.objects.create(
            contrato=c2, cliente=self.c_logistica,
            descricao=f'Locação — {c2.numero}',
            valor_total=Decimal('4340.00'),  # 1200 + 1540 + extras
            valor_pago=Decimal('1200.00'),
            data_emissao=saida_2.date(), data_vencimento=dev_prev_2.date(),
            situacao='pago_parcial',
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_compass, km=5600, contrato=c2,
            data=saida_2, origem='contrato_saida', registrado_por=self.admin_user,
        )

        # ── CONTRATO 3: AGUARDANDO DEVOLUÇÃO (Civic + Pedro, atrasado) ───────
        saida_3 = agora - timedelta(days=15)
        dev_prev_3 = agora - timedelta(days=5)  # passou da data — em atraso!

        res_3 = Reserva.objects.create(
            cliente=self.c_pedro, grupo_veiculo=self.g_sedan,
            veiculo=self.v_civic, situacao='ativa', canal='telefone',
            data_retirada=saida_3, data_devolucao=dev_prev_3,
            diaria_cotada=Decimal('160.00'), caucao_cotado=Decimal('800.00'),
        )
        c3 = Contrato.objects.create(
            numero='AF-2026-0003', reserva=res_3,
            cliente=self.c_pedro, veiculo=self.v_civic,
            situacao='aguardando_devolucao', criado_por=self.admin_user,
            data_saida=saida_3, km_saida=30800, combustivel_saida='cheio',
            data_devolucao_prevista=dev_prev_3,
            diaria=Decimal('160.00'), km_franquia_diaria=250,
            valor_km_excedente=Decimal('2.00'),
            caucao_valor=Decimal('800.00'), caucao_situacao='pago',
            caucao_pago_em=saida_3 + timedelta(minutes=20),
        )
        for i, (venc, sit) in enumerate([
            (saida_3.date(), 'pago'),
            (saida_3.date() + timedelta(days=7), 'pago'),
            (saida_3.date() + timedelta(days=14), 'em_atraso'),
        ], start=1):
            pago = sit == 'pago'
            kwargs = dict(
                contrato=c3, numero=i,
                tipo='caucao' if i == 1 else 'semanal',
                data_vencimento=venc, valor=Decimal('800.00') if i == 1 else Decimal('1120.00'),
                situacao=sit, origem='original',
            )
            if pago:
                kwargs['data_pagamento'] = timezone.make_aware(
                    __import__('datetime').datetime.combine(venc, __import__('datetime').time(10, 0))
                )
                kwargs['forma_pagamento'] = 'dinheiro'
            ParcelaContrato.objects.create(**kwargs)
        PagamentoContrato.objects.create(
            contrato=c3, forma_pagamento='dinheiro', tipo='caucao',
            valor=Decimal('800.00'), data_pagamento=saida_3 + timedelta(minutes=20),
        )
        PagamentoContrato.objects.create(
            contrato=c3, forma_pagamento='dinheiro', tipo='locacao',
            valor=Decimal('1120.00'), data_pagamento=saida_3 + timedelta(days=7, hours=2),
        )
        ContaReceber.objects.create(
            contrato=c3, cliente=self.c_pedro,
            descricao=f'Locação — {c3.numero}',
            valor_total=Decimal('3040.00'),
            valor_pago=Decimal('1920.00'),
            data_emissao=saida_3.date(), data_vencimento=dev_prev_3.date(),
            situacao='vencido',
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_civic, km=30800, contrato=c3,
            data=saida_3, origem='contrato_saida', registrado_por=self.admin_user,
        )

        # ── CONTRATO 4: ABERTO (João + Gol — checkout pendente) ───────────────
        c4 = Contrato.objects.create(
            numero='AF-2026-0004',
            cliente=self.c_joao, veiculo=self.v_gol,
            situacao='aberto', criado_por=self.admin_user,
            data_devolucao_prevista=agora + timedelta(days=7),
            diaria=Decimal('120.00'), km_franquia_diaria=200,
            valor_km_excedente=Decimal('1.50'),
            caucao_valor=Decimal('500.00'), caucao_situacao='pendente',
        )
        ContaReceber.objects.create(
            contrato=c4, cliente=self.c_joao,
            descricao=f'Locação — {c4.numero}',
            valor_total=Decimal('1340.00'),
            valor_pago=Decimal('0.00'),
            data_emissao=hoje, data_vencimento=(agora + timedelta(days=7)).date(),
            situacao='pendente',
        )

        # ── CONTRATO 5: ENCERRADO PAGO (Maria + Yaris, 30 dias atrás) ────────
        saida_5 = agora - timedelta(days=30)
        dev_5    = agora - timedelta(days=23)

        res_5 = Reserva.objects.create(
            cliente=self.c_maria, grupo_veiculo=self.g_hatch,
            veiculo=self.v_yaris, situacao='concluida', canal='whatsapp',
            data_retirada=saida_5, data_devolucao=dev_5,
            diaria_cotada=Decimal('120.00'), caucao_cotado=Decimal('500.00'),
        )
        c5 = Contrato.objects.create(
            numero='AF-2026-0005', reserva=res_5,
            cliente=self.c_maria, veiculo=self.v_yaris,
            situacao='encerrado', criado_por=self.admin_user,
            data_saida=saida_5, km_saida=53500, combustivel_saida='cheio',
            data_devolucao_prevista=dev_5,
            data_devolucao_real=dev_5,
            km_devolucao=54200, combustivel_devolucao='3/4',
            diaria=Decimal('120.00'), km_franquia_diaria=200,
            valor_km_excedente=Decimal('1.50'),
            caucao_valor=Decimal('500.00'), caucao_situacao='devolvido',
            caucao_pago_em=saida_5 + timedelta(minutes=25),
            total_dias=7, km_total=700, km_excedente=300,
            valor_km_excedente_total=Decimal('450.00'),
        )
        for i, venc in enumerate([saida_5.date(), saida_5.date() + timedelta(days=7)], start=1):
            ParcelaContrato.objects.create(
                contrato=c5, numero=i,
                tipo='caucao' if i == 1 else 'semanal',
                data_vencimento=venc,
                valor=Decimal('500.00') if i == 1 else Decimal('840.00'),
                situacao='pago',
                data_pagamento=timezone.make_aware(
                    __import__('datetime').datetime.combine(venc, __import__('datetime').time(14, 0))
                ),
                forma_pagamento='pix', origem='original',
            )
        PagamentoContrato.objects.create(
            contrato=c5, forma_pagamento='pix', tipo='caucao',
            valor=Decimal('500.00'), data_pagamento=saida_5 + timedelta(minutes=25),
        )
        PagamentoContrato.objects.create(
            contrato=c5, forma_pagamento='pix', tipo='locacao',
            valor=Decimal('840.00'), data_pagamento=dev_5 - timedelta(hours=1),
        )
        PagamentoContrato.objects.create(
            contrato=c5, forma_pagamento='pix', tipo='locacao',
            valor=Decimal('450.00'), data_pagamento=dev_5,
            observacoes='Pagamento de KM excedente — 300 km × R$1,50',
        )
        ContaReceber.objects.create(
            contrato=c5, cliente=self.c_maria,
            descricao=f'Locação — {c5.numero}',
            valor_total=Decimal('1290.00'),
            valor_pago=Decimal('1290.00'),
            data_emissao=saida_5.date(), data_vencimento=dev_5.date(),
            situacao='pago',
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_yaris, km=53500, contrato=c5,
            data=saida_5, origem='contrato_saida', registrado_por=self.admin_user,
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_yaris, km=54200, contrato=c5,
            data=dev_5, origem='contrato_devolucao', registrado_por=self.admin_user,
        )

        # ── CONTRATO 6: ENCERRADO COM AVARIA (Ana + HR-V, 60 dias atrás) ─────
        saida_6 = agora - timedelta(days=60)
        dev_6    = agora - timedelta(days=50)

        res_6 = Reserva.objects.create(
            cliente=self.c_ana, grupo_veiculo=self.g_suv,
            veiculo=self.v_hrv, situacao='concluida', canal='site',
            data_retirada=saida_6, data_devolucao=dev_6,
            diaria_cotada=Decimal('220.00'), caucao_cotado=Decimal('1200.00'),
        )
        c6 = Contrato.objects.create(
            numero='AF-2026-0006', reserva=res_6,
            cliente=self.c_ana, veiculo=self.v_hrv,
            situacao='encerrado', criado_por=self.admin_user,
            data_saida=saida_6, km_saida=10500, combustivel_saida='cheio',
            data_devolucao_prevista=dev_6,
            data_devolucao_real=dev_6,
            km_devolucao=12000, combustivel_devolucao='1/2',
            diaria=Decimal('220.00'), km_franquia_diaria=300,
            valor_km_excedente=Decimal('2.50'),
            caucao_valor=Decimal('1200.00'), caucao_situacao='retido',
            caucao_pago_em=saida_6 + timedelta(hours=1),
            total_dias=10, km_total=1500, km_excedente=0,
            valor_diferenca_combustivel=Decimal('120.00'),
        )
        AvariaContrato.objects.create(
            contrato=c6, descricao='Amassado na para-choque traseiro',
            localizacao='Para-choque traseiro', valor_cobrado=Decimal('800.00'),
            situacao='cobrada',
        )
        AvariaContrato.objects.create(
            contrato=c6, descricao='Arranhão na porta traseira esquerda',
            localizacao='Porta traseira esquerda', valor_cobrado=Decimal('350.00'),
            situacao='paga',
        )
        for i, venc in enumerate([saida_6.date(), saida_6.date() + timedelta(days=7)], start=1):
            ParcelaContrato.objects.create(
                contrato=c6, numero=i,
                tipo='caucao' if i == 1 else 'semanal',
                data_vencimento=venc,
                valor=Decimal('1200.00') if i == 1 else Decimal('1540.00'),
                situacao='pago',
                data_pagamento=timezone.make_aware(
                    __import__('datetime').datetime.combine(venc, __import__('datetime').time(11, 0))
                ),
                forma_pagamento='cartao_credito', origem='original',
            )
        PagamentoContrato.objects.create(
            contrato=c6, forma_pagamento='cartao_credito', tipo='caucao',
            valor=Decimal('1200.00'), data_pagamento=saida_6 + timedelta(hours=1),
        )
        PagamentoContrato.objects.create(
            contrato=c6, forma_pagamento='cartao_credito', tipo='locacao',
            valor=Decimal('1540.00'), data_pagamento=dev_6 - timedelta(hours=2),
        )
        PagamentoContrato.objects.create(
            contrato=c6, forma_pagamento='pix', tipo='avaria',
            valor=Decimal('350.00'), data_pagamento=dev_6,
            observacoes='Pagamento avaria porta traseira esq.',
        )
        ContaReceber.objects.create(
            contrato=c6, cliente=self.c_ana,
            descricao=f'Locação — {c6.numero}',
            valor_total=Decimal('3090.00'),
            valor_pago=Decimal('3090.00'),
            data_emissao=saida_6.date(), data_vencimento=dev_6.date(),
            situacao='pago',
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_hrv, km=10500, contrato=c6,
            data=saida_6, origem='contrato_saida', registrado_por=self.admin_user,
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_hrv, km=12000, contrato=c6,
            data=dev_6, origem='contrato_devolucao', registrado_por=self.admin_user,
        )

        # ── CONTRATO 7: ENCERRADO ANTIGO (para enriquecer relatórios) ────────
        saida_7 = agora - timedelta(days=90)
        dev_7    = agora - timedelta(days=76)

        res_7 = Reserva.objects.create(
            cliente=self.c_alpha, grupo_veiculo=self.g_sedan,
            veiculo=self.v_civic, situacao='concluida', canal='balcao',
            data_retirada=saida_7, data_devolucao=dev_7,
            diaria_cotada=Decimal('160.00'), caucao_cotado=Decimal('800.00'),
        )
        c7 = Contrato.objects.create(
            numero='AF-2026-0007', reserva=res_7,
            cliente=self.c_alpha, veiculo=self.v_civic,
            situacao='encerrado', criado_por=self.admin_user,
            data_saida=saida_7, km_saida=28500, combustivel_saida='cheio',
            data_devolucao_prevista=dev_7,
            data_devolucao_real=dev_7,
            km_devolucao=31500, combustivel_devolucao='cheio',
            diaria=Decimal('160.00'), km_franquia_diaria=250,
            valor_km_excedente=Decimal('2.00'),
            caucao_valor=Decimal('800.00'), caucao_situacao='devolvido',
            caucao_pago_em=saida_7 + timedelta(minutes=30),
            total_dias=14, km_total=3000, km_excedente=500,
            valor_km_excedente_total=Decimal('1000.00'),
        )
        PagamentoContrato.objects.create(
            contrato=c7, forma_pagamento='pix', tipo='caucao',
            valor=Decimal('800.00'), data_pagamento=saida_7 + timedelta(minutes=30),
        )
        PagamentoContrato.objects.create(
            contrato=c7, forma_pagamento='pix', tipo='locacao',
            valor=Decimal('2240.00'), data_pagamento=saida_7 + timedelta(days=7),
        )
        PagamentoContrato.objects.create(
            contrato=c7, forma_pagamento='pix', tipo='locacao',
            valor=Decimal('2240.00'), data_pagamento=dev_7,
        )
        PagamentoContrato.objects.create(
            contrato=c7, forma_pagamento='pix', tipo='locacao',
            valor=Decimal('1000.00'), data_pagamento=dev_7,
            observacoes='KM excedente 500 km × R$2,00',
        )
        ContaReceber.objects.create(
            contrato=c7, cliente=self.c_alpha,
            descricao=f'Locação — {c7.numero}',
            valor_total=Decimal('6280.00'),
            valor_pago=Decimal('6280.00'),
            data_emissao=saida_7.date(), data_vencimento=dev_7.date(),
            situacao='pago',
        )

        # ── CONTRATO 8: CANCELADO ─────────────────────────────────────────────
        Contrato.objects.create(
            numero='AF-2026-0008',
            cliente=self.c_lucia, veiculo=self.v_onix,
            situacao='cancelado', criado_por=self.admin_user,
            data_devolucao_prevista=agora + timedelta(days=5),
            diaria=Decimal('120.00'), km_franquia_diaria=200,
            valor_km_excedente=Decimal('1.50'),
            caucao_valor=Decimal('500.00'), caucao_situacao='pendente',
        )

        from apps.contracts.models import Contrato as C, PagamentoContrato as P, ParcelaContrato as PC
        self.stdout.write(
            f'    {C.objects.count()} contratos, '
            f'{PC.objects.count()} parcelas, '
            f'{P.objects.count()} pagamentos'
        )

    # ─── Financeiro ───────────────────────────────────────────────────────────

    def _criar_financeiro(self):
        from apps.financeiro.models import DespesaOperacional, MultaTransito
        from apps.contracts.models import Contrato

        self.stdout.write('  Criando dados financeiros...')
        hoje = timezone.now().date()

        c1 = Contrato.objects.get(numero='AF-2026-0001')
        c3 = Contrato.objects.get(numero='AF-2026-0003')

        despesas = [
            # Manutenção (vinculada a veículos)
            ('manutencao', 'Troca de embreagem e revisão geral — Cruze LT',
             self.v_cruze, Decimal('1850.00'), hoje - timedelta(days=3), hoje - timedelta(days=3)),
            ('manutencao', 'Troca de óleo 5W30 e filtro de óleo — Gol',
             self.v_gol, Decimal('185.00'), hoje - timedelta(days=35), hoje - timedelta(days=35)),
            ('manutencao', 'Balanceamento e alinhamento — Argo',
             self.v_argo, Decimal('120.00'), hoje - timedelta(days=20), hoje - timedelta(days=20)),
            # Seguro
            ('seguro', 'Renovação seguro anual — Corolla 2023',
             self.v_corolla, Decimal('3200.00'), date(2026, 1, 15), date(2026, 1, 15)),
            ('seguro', 'Renovação seguro anual — HR-V',
             self.v_hrv, Decimal('4100.00'), date(2026, 2, 5), date(2026, 2, 5)),
            # IPVA
            ('ipva', 'IPVA 2026 — Volkswagen Gol',
             self.v_gol, Decimal('1380.00'), date(2026, 3, 10), date(2026, 3, 10)),
            ('ipva', 'IPVA 2026 — Toyota Corolla',
             self.v_corolla, Decimal('4350.00'), date(2026, 3, 10), date(2026, 3, 10)),
            # Licenciamento
            ('licenciamento', 'CRLV 2026 — Honda HR-V',
             self.v_hrv, Decimal('98.00'), date(2026, 4, 1), date(2026, 4, 1)),
            # Combustível
            ('combustivel', 'Abastecimento preparação entrega — Argo',
             self.v_argo, Decimal('95.00'), hoje, None),
            ('combustivel', 'Abastecimento revisão — Yaris',
             self.v_yaris, Decimal('72.00'), hoje - timedelta(days=5), hoje - timedelta(days=5)),
            # Lavagem
            ('lavagem', 'Higienização completa + cera — HR-V',
             self.v_hrv, Decimal('280.00'), hoje - timedelta(days=1), hoje - timedelta(days=1)),
            ('lavagem', 'Lavagem simples pós-devolução — Yaris',
             self.v_yaris, Decimal('45.00'), hoje - timedelta(days=23), hoje - timedelta(days=23)),
            # Administrativo (sem veículo)
            ('salario', 'Salário atendente — Maio/2026',
             None, Decimal('2800.00'), hoje.replace(day=1), None),
            ('salario', 'Salário mecânico — Maio/2026',
             None, Decimal('3200.00'), hoje.replace(day=1), None),
            ('aluguel', 'Aluguel sede — Maio/2026',
             None, Decimal('5500.00'), hoje.replace(day=1), hoje.replace(day=5)),
            ('marketing', 'Impulsionamento redes sociais — Maio/2026',
             None, Decimal('800.00'), hoje - timedelta(days=10), hoje - timedelta(days=10)),
            ('outros', 'Material de escritório e EPIs',
             None, Decimal('320.00'), hoje - timedelta(days=15), hoje - timedelta(days=15)),
        ]
        for cat, desc, veiculo, valor, comp, pag in despesas:
            DespesaOperacional.objects.create(
                categoria=cat, descricao=desc, veiculo=veiculo, valor=valor,
                data_competencia=comp, data_pagamento=pag,
                criado_por=self.admin_user,
            )

        # Multas de trânsito
        MultaTransito.objects.create(
            veiculo=self.v_corolla, contrato=c1,
            numero_auto='AIT-2026-00123',
            data_infracao=hoje - timedelta(days=5),
            data_notificacao=hoje - timedelta(days=2),
            prazo_indicacao=hoje + timedelta(days=28),
            descricao='Excesso de velocidade — 51% a 80% acima do limite',
            pontos=5, valor=Decimal('293.47'),
            condutor_nome='Carlos Eduardo Ferreira', condutor_cpf='345.678.901-23',
            situacao='cobrada_cliente',
        )
        MultaTransito.objects.create(
            veiculo=self.v_gol, contrato=None,
            numero_auto='AIT-2026-00456',
            data_infracao=date(2026, 4, 10),
            descricao='Estacionamento em local proibido — Zona Azul',
            pontos=3, valor=Decimal('195.23'),
            situacao='pendente_identificacao',
        )
        MultaTransito.objects.create(
            veiculo=self.v_civic, contrato=c3,
            numero_auto='AIT-2026-00789',
            data_infracao=hoje - timedelta(days=12),
            data_notificacao=hoje - timedelta(days=8),
            prazo_indicacao=hoje + timedelta(days=3),  # prazo crítico!
            descricao='Avanço de sinal vermelho em cruzamento',
            pontos=7, valor=Decimal('488.00'),
            condutor_nome='Pedro Henrique Matos', condutor_cpf='567.890.123-45',
            situacao='identificada',
        )
        MultaTransito.objects.create(
            veiculo=self.v_hrv, contrato=None,
            numero_auto='AIT-2025-09981',
            data_infracao=date(2025, 11, 20),
            descricao='Uso de celular ao volante',
            pontos=5, valor=Decimal('293.47'),
            situacao='paga',
        )

        self.stdout.write(
            f'    {DespesaOperacional.objects.count()} despesas, '
            f'{MultaTransito.objects.count()} multas'
        )

    # ─── Manutenção ───────────────────────────────────────────────────────────

    def _criar_manutencao(self):
        from apps.manutencao.models import OrdemManutencao, AlertaManutencao
        from apps.contracts.models import Contrato
        from apps.fleet.models import HistoricoKmVeiculo

        self.stdout.write('  Criando manutenções...')
        hoje = timezone.now().date()
        c1 = Contrato.objects.get(numero='AF-2026-0001')

        OrdemManutencao.objects.create(
            veiculo=self.v_cruze, tipo='corretiva', situacao='em_andamento',
            descricao='Troca de embreagem e revisão geral dos 40.000 km',
            km_na_manutencao=42000,
            data_entrada=hoje - timedelta(days=3),
            fornecedor='Auto Center SP — Unidade Santana',
            custo_total=Decimal('1850.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_hrv, tipo='preventiva', situacao='agendada',
            descricao='Revisão 12.000 km — troca de óleo e filtros',
            km_na_manutencao=12000,
            data_agendada=hoje + timedelta(days=13),
            fornecedor='Concessionária Honda São Paulo',
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_corolla, contrato=c1, tipo='sinistro', situacao='agendada',
            descricao='Reparo arranhão lateral direito — avaria durante locação',
            km_na_manutencao=18900,
            data_agendada=hoje + timedelta(days=5),
            fornecedor='Funilaria Paulista',
            custo_total=Decimal('450.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_gol, tipo='preventiva', situacao='concluida',
            descricao='Troca de óleo 5W30 e filtro de ar — 25.000 km',
            km_na_manutencao=25000,
            data_entrada=hoje - timedelta(days=35), data_saida=hoje - timedelta(days=35),
            fornecedor='Troca Rápida Lubrificantes',
            custo_total=Decimal('185.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_argo, tipo='preventiva', situacao='concluida',
            descricao='Balanceamento, alinhamento e rodízio de pneus',
            km_na_manutencao=35000,
            data_entrada=hoje - timedelta(days=20), data_saida=hoje - timedelta(days=20),
            fornecedor='Pneus Brasil — Zona Norte',
            custo_total=Decimal('120.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_yaris, tipo='preventiva', situacao='agendada',
            descricao='Revisão dos 55.000 km — troca pastilhas de freio e fluido',
            km_na_manutencao=55000,
            data_agendada=hoje + timedelta(days=20),
            fornecedor='Concessionária Toyota Norte',
        )

        # Alertas (alguns vencidos, alguns próximos, alguns ok)
        AlertaManutencao.objects.create(
            veiculo=self.v_corolla, tipo_alerta='km',
            descricao='Troca de óleo a cada 10.000 km',
            km_proximo_servico=25000, km_intervalo=10000, ativo=True,  # vencido (km atual 18900 → nope, próximo)
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_gol, tipo_alerta='data',
            descricao='Revisão anual preventiva',
            data_proximo_servico=date(2026, 8, 1), ativo=True,
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_cruze, tipo_alerta='km',
            descricao='Troca de correia dentada a cada 80.000 km',
            km_proximo_servico=80000, km_intervalo=80000, ativo=True,
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_hrv, tipo_alerta='data',
            descricao='Vencimento do extintor',
            data_proximo_servico=date(2026, 6, 5), ativo=True,  # próximo de vencer
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_yaris, tipo_alerta='km',
            descricao='Troca de óleo a cada 10.000 km',
            km_proximo_servico=55000, km_intervalo=10000, ativo=True,  # vencido!
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_compass, tipo_alerta='data',
            descricao='Revisão dos 6 meses na concessionária',
            data_proximo_servico=date(2026, 3, 18), ativo=True,  # vencido!
        )

        # Histórico KM manual para alguns veículos
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_gol, km=25000, origem='manutencao',
            data=timezone.make_aware(
                __import__('datetime').datetime.combine(hoje - timedelta(days=35), __import__('datetime').time(9, 0))
            ),
            observacao='Entrada na oficina — troca de óleo',
            registrado_por=self.admin_user,
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_gol, km=25100, origem='manual',
            data=timezone.now() - timedelta(days=2),
            observacao='KM atualizado manualmente após vistoria',
            registrado_por=self.admin_user,
        )
        HistoricoKmVeiculo.objects.create(
            veiculo=self.v_cruze, km=42000, origem='manutencao',
            data=timezone.make_aware(
                __import__('datetime').datetime.combine(hoje - timedelta(days=3), __import__('datetime').time(8, 30))
            ),
            observacao='Entrada na oficina — troca de embreagem',
            registrado_por=self.admin_user,
        )

        self.stdout.write(
            f'    {OrdemManutencao.objects.count()} ordens, '
            f'{AlertaManutencao.objects.count()} alertas'
        )

    # ─── Resumo ───────────────────────────────────────────────────────────────

    def _imprimir_resumo(self):
        from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo, DocumentoVeiculo, HistoricoKmVeiculo
        from apps.customers.models import Cliente, CNHCliente
        from apps.contracts.models import Reserva, Contrato, ParcelaContrato, PagamentoContrato, AvariaContrato
        from apps.financeiro.models import ContaReceber, DespesaOperacional, MultaTransito
        from apps.manutencao.models import OrdemManutencao, AlertaManutencao
        from django.contrib.auth.models import User

        W = self.stdout.write
        S = self.style.SUCCESS
        sep  = '  ' + '=' * 53
        sep2 = '  ' + '-' * 53

        W('\n' + sep)
        W(S('  RESUMO DOS DADOS DE DEMONSTRACAO'))
        W(sep)
        W(f'  Usuarios               : {User.objects.count()}  (admin/atendente/financeiro/mecanico)')
        W(sep2)
        W(f'  Categorias             : {CategoriaVeiculo.objects.count()}')
        W(f'  Grupos de veiculo      : {GrupoVeiculo.objects.count()}')
        W(f'  Veiculos               : {Veiculo.objects.count()}')
        W(f'  Documentos de veiculos : {DocumentoVeiculo.objects.count()}')
        W(f'  Historico de KM        : {HistoricoKmVeiculo.objects.count()} registros')
        W(sep2)
        W(f'  Clientes               : {Cliente.objects.count()}  (PF e PJ, 1 bloqueado)')
        W(f'  CNHs cadastradas       : {CNHCliente.objects.count()}  (1 vencida)')
        W(sep2)
        W(f'  Reservas               : {Reserva.objects.count()}')
        W(f'  Contratos              : {Contrato.objects.count()}')
        for sit, label in [('aberto','Aberto'), ('ativo','Ativo'),
                           ('aguardando_devolucao','Ag. Devolucao'),
                           ('encerrado','Encerrado'), ('cancelado','Cancelado')]:
            n = Contrato.objects.filter(situacao=sit).count()
            W(f'    > {label:<22}: {n}')
        W(f'  Parcelas               : {ParcelaContrato.objects.count()}')
        W(f'  Pagamentos             : {PagamentoContrato.objects.count()}')
        W(f'  Avarias                : {AvariaContrato.objects.count()}')
        W(sep2)
        W(f'  Contas a receber       : {ContaReceber.objects.count()}')
        for sit, label in [('pendente','Pendente'), ('pago_parcial','Pago Parcial'),
                           ('pago','Pago'), ('vencido','Vencido')]:
            n = ContaReceber.objects.filter(situacao=sit).count()
            W(f'    > {label:<22}: {n}')
        W(f'  Despesas               : {DespesaOperacional.objects.count()}  (todas as categorias)')
        W(f'  Multas de transito     : {MultaTransito.objects.count()}  (pend/identif/cobrada/paga)')
        W(sep2)
        W(f'  Ordens de manutencao   : {OrdemManutencao.objects.count()}')
        W(f'  Alertas preventivos    : {AlertaManutencao.objects.count()}  (2 vencidos, 1 proximo)')
        W(sep)
        W(S('  ACESSOS:'))
        W('  Dashboard  : http://localhost:8000/')
        W('  Usuarios   : admin / admin123')
        W('              atendente / senha123')
        W('              financeiro / senha123')
        W('              mecanico   / senha123')
        W(sep + '\n')
