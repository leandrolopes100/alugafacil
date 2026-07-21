"""
Management command para popular o banco com dados de demonstracao,
sem criar usuarios e sem apagar nada existente.

Cobre: frota (12 veiculos), clientes (8), reservas, contratos (6, em
todos os estados), parcelas/pagamentos/contas a receber, despesas
operacionais (simples e parceladas, com ParcelaDespesa), multas de
transito, manutencao (ordens e alertas) e investidores (vinculos e
cobrancas de gestao) — com datas retroativas e atuais.

Usa o primeiro superusuario existente como "criado_por"/"registrado_por".
Nao cria nem apaga usuarios. Nao apaga nenhum dado existente. Numeros de
contrato sao gerados automaticamente pelo model (sem risco de colisao
com contratos ja existentes).

Uso:
    python manage.py popular_fixtures
"""
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db.models.signals import post_save
from django.utils import timezone
from datetime import date, timedelta, datetime, time


class Command(BaseCommand):
    help = 'Popula o banco com dados de demonstracao (sem usuarios, sem apagar nada existente)'

    def handle(self, *args, **options):
        from django.contrib.auth.models import User
        self.admin_user = User.objects.filter(is_superuser=True).order_by('id').first() \
            or User.objects.order_by('id').first()
        if not self.admin_user:
            raise CommandError(
                'Nenhum usuario encontrado no banco. Crie um usuario antes de rodar este comando.'
            )

        from apps.contracts.signals import contrato_post_save, pagamento_post_save
        from apps.contracts.models import Contrato, PagamentoContrato
        post_save.disconnect(contrato_post_save, sender=Contrato)
        post_save.disconnect(pagamento_post_save, sender=PagamentoContrato)

        try:
            self._criar_configuracao()
            self._criar_frota()
            self._criar_clientes()
            self._criar_reservas()
            self._criar_contratos()
            self._criar_financeiro()
            self._criar_manutencao()
            self._criar_investidores()
        finally:
            post_save.connect(contrato_post_save, sender=Contrato)
            post_save.connect(pagamento_post_save, sender=PagamentoContrato)

        self.stdout.write(self.style.SUCCESS('\n  Fixtures criadas com sucesso!'))
        self._imprimir_resumo()

    # ─── Configuracao ─────────────────────────────────────────────────────────

    def _criar_configuracao(self):
        from apps.financeiro.models import ConfiguracaoLocadora
        ConfiguracaoLocadora.obter()

    # ─── Frota (12 veiculos) ────────────────────────────────────────────────────

    def _criar_frota(self):
        from apps.fleet.models import CategoriaVeiculo, GrupoVeiculo, Veiculo, DocumentoVeiculo

        self.stdout.write('  Criando frota...')

        cat_eco = CategoriaVeiculo.objects.create(
            nome='Economico', icone='bi-car-front', cor='#3B82F6', ordem=1)
        cat_med = CategoriaVeiculo.objects.create(
            nome='Intermediario', icone='bi-car-front-fill', cor='#10B981', ordem=2)
        cat_suv = CategoriaVeiculo.objects.create(
            nome='SUV / Premium', icone='bi-truck', cor='#F59E0B', ordem=3)

        self.g_hatch = GrupoVeiculo.objects.create(
            nome='Hatch Economico', categoria=cat_eco,
            diaria=Decimal('120.00'), semanal=Decimal('750.00'), mensal=Decimal('2800.00'),
            caucao=Decimal('500.00'), km_franquia_diaria=200,
            valor_km_excedente=Decimal('1.50'),
            descricao='Veiculos compactos com otimo custo-beneficio.',
        )
        self.g_sedan = GrupoVeiculo.objects.create(
            nome='Sedan Intermediario', categoria=cat_med,
            diaria=Decimal('160.00'), semanal=Decimal('1000.00'), mensal=Decimal('3800.00'),
            caucao=Decimal('800.00'), km_franquia_diaria=250,
            valor_km_excedente=Decimal('2.00'),
            descricao='Sedans confortaveis para viagens longas.',
        )
        self.g_suv = GrupoVeiculo.objects.create(
            nome='SUV Premium', categoria=cat_suv,
            diaria=Decimal('220.00'), semanal=Decimal('1400.00'), mensal=Decimal('5200.00'),
            caucao=Decimal('1200.00'), km_franquia_diaria=300,
            valor_km_excedente=Decimal('2.50'),
            descricao='SUVs e picapes premium para maior conforto e espaco.',
        )

        # ── 12 veiculos ──────────────────────────────────────────────────────
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
            cor='Azul Topazio', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=8200, situacao='disponivel',
            data_aquisicao=date(2023, 5, 20), valor_aquisicao=Decimal('82000.00'),
            valor_fipe=Decimal('78000.00'),
        )
        self.v_corolla = Veiculo.objects.create(
            placa='DEF2E34', marca='Toyota', modelo='Corolla XEi',
            grupo=self.g_sedan, ano_fabricacao=2023, ano_modelo=2023,
            cor='Prata Metalico', combustivel='flex', transmissao='automatico',
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
        self.v_kicks = Veiculo.objects.create(
            placa='EFG5P67', marca='Nissan', modelo='Kicks Advance',
            grupo=self.g_suv, ano_fabricacao=2022, ano_modelo=2023,
            cor='Prata Solar', combustivel='flex', transmissao='cvt',
            portas=4, lugares=5, km_atual=38500, situacao='manutencao',
            data_aquisicao=date(2022, 10, 8), valor_aquisicao=Decimal('142000.00'),
        )
        self.v_tracker = Veiculo.objects.create(
            placa='HIJ6Q78', marca='Chevrolet', modelo='Tracker Premier',
            grupo=self.g_suv, ano_fabricacao=2021, ano_modelo=2021,
            cor='Preto Ouro Negro', combustivel='flex', transmissao='automatico',
            portas=4, lugares=5, km_atual=61000, situacao='inativo',
            data_aquisicao=date(2021, 5, 22), valor_aquisicao=Decimal('125000.00'),
        )

        docs = [
            (self.v_gol,    'crlv',    '2025000001', date(2026, 12, 31)),
            (self.v_gol,    'seguro',  'AP-00123456', date(2026, 8, 10)),
            (self.v_corolla,'crlv',    '2025000002', date(2026, 12, 31)),
            (self.v_corolla,'seguro',  'AP-00234567', date(2027, 1, 20)),
            (self.v_hrv,    'crlv',    '2025000003', date(2026, 12, 31)),
            (self.v_hrv,    'vistoria','VI-00001234', date(2026, 6, 5)),   # proximo de vencer
            (self.v_cruze,  'crlv',    '2025000004', date(2025, 12, 31)), # VENCIDO
            (self.v_argo,   'crlv',    '2025000005', date(2026, 12, 31)),
            (self.v_argo,   'extintor','EX-00099881', date(2026, 9, 15)),
            (self.v_compass,'crlv',    '2025000006', date(2026, 12, 31)),
            (self.v_compass,'seguro',  'AP-00345678', date(2027, 9, 18)),
            (self.v_kicks,  'crlv',    '2025000007', date(2025, 11, 30)), # VENCIDO
            (self.v_tracker,'crlv',    '2025000008', date(2026, 12, 31)),
        ]
        for veiculo, tipo, numero, validade in docs:
            DocumentoVeiculo.objects.create(
                veiculo=veiculo, tipo=tipo, numero=numero,
                data_validade=validade, dias_alerta=30,
            )

        self.stdout.write(f'    {Veiculo.objects.filter(pk__in=[v.pk for v in [self.v_gol, self.v_argo, self.v_onix, self.v_corolla, self.v_civic, self.v_cruze, self.v_hrv, self.v_compass, self.v_yaris, self.v_t_cross, self.v_kicks, self.v_tracker]]).count()} veiculos criados')

    # ─── Clientes (8) ─────────────────────────────────────────────────────────

    def _criar_clientes(self):
        from apps.customers.models import Cliente, CNHCliente

        self.stdout.write('  Criando clientes...')

        self.c_joao = Cliente.objects.create(
            tipo='pf', nome='Joao Carlos Silva',
            cpf='123.456.789-01', data_nascimento=date(1985, 3, 15),
            email='joao.silva@email.com', celular='(11) 98765-4321',
            logradouro='Rua das Flores', numero='123', bairro='Jardim Primavera',
            cidade='Sao Paulo', estado='SP', cep='01234-567', situacao='ativo',
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
            cidade='Sao Paulo', estado='SP', cep='04101-000', situacao='ativo',
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
            bairro='Bela Vista', cidade='Sao Paulo', estado='SP', cep='01310-100',
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
            cidade='Sao Paulo', estado='SP', cep='04029-000', situacao='ativo',
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
            categoria='b', validade=date(2025, 6, 15),  # CNH VENCIDA
            principal=True,
        )

        # PJ
        self.c_alpha = Cliente.objects.create(
            tipo='pj', razao_social='Construtora Alpha LTDA',
            cnpj='12.345.678/0001-90', contato='Roberto Alves',
            email='financeiro@alphaconstrucao.com.br', telefone='(11) 3234-5678',
            logradouro='Av. Brigadeiro Faria Lima', numero='3500',
            bairro='Itaim Bibi', cidade='Sao Paulo', estado='SP', cep='04538-132',
            situacao='ativo',
        )
        self.c_logistica = Cliente.objects.create(
            tipo='pj', razao_social='Logistica Express S/A',
            cnpj='98.765.432/0001-10', contato='Fernanda Lima',
            email='operacoes@logisticaexpress.com', telefone='(11) 3456-9870',
            logradouro='Rod. Anhanguera', numero='1200', bairro='Distrito Industrial',
            cidade='Campinas', estado='SP', cep='13032-200',
            situacao='ativo',
        )

        # Cliente bloqueado
        self.c_bloqueado = Cliente.objects.create(
            tipo='pf', nome='Marcos Andrade Pereira',
            cpf='789.012.345-67', data_nascimento=date(1975, 5, 20),
            email='marcos.pereira@email.com', celular='(11) 93210-8765',
            logradouro='Rua XV de Novembro', numero='300',
            bairro='Centro', cidade='Sao Paulo', estado='SP', cep='01013-001',
            situacao='bloqueado',
            motivo_bloqueio='Contrato anterior encerrado com avaria nao quitada.',
        )

        self.stdout.write('    8 clientes criados')

    # ─── Reservas ─────────────────────────────────────────────────────────────

    def _criar_reservas(self):
        from apps.contracts.models import Reserva
        self.stdout.write('  Criando reservas...')

        self.res_maria = Reserva.objects.create(
            cliente=self.c_maria, grupo_veiculo=self.g_hatch,
            veiculo=self.v_argo, situacao='confirmada', canal='whatsapp',
            data_retirada=timezone.now() + timedelta(days=3),
            data_devolucao=timezone.now() + timedelta(days=17),
            diaria_cotada=Decimal('120.00'), caucao_cotado=Decimal('500.00'),
        )
        Reserva.objects.create(
            cliente=self.c_alpha, grupo_veiculo=self.g_suv,
            veiculo=None, situacao='pendente', canal='balcao',
            data_retirada=timezone.now() + timedelta(days=10),
            data_devolucao=timezone.now() + timedelta(days=24),
            diaria_cotada=Decimal('220.00'), caucao_cotado=Decimal('1200.00'),
        )
        Reserva.objects.create(
            cliente=self.c_ana, grupo_veiculo=self.g_sedan,
            veiculo=None, situacao='pendente', canal='site',
            data_retirada=timezone.now() + timedelta(days=15),
            data_devolucao=timezone.now() + timedelta(days=22),
            diaria_cotada=Decimal('160.00'), caucao_cotado=Decimal('800.00'),
        )
        Reserva.objects.create(
            cliente=self.c_pedro, grupo_veiculo=self.g_hatch,
            veiculo=self.v_yaris, situacao='no_show', canal='telefone',
            data_retirada=timezone.now() - timedelta(days=5),
            data_devolucao=timezone.now() + timedelta(days=2),
            diaria_cotada=Decimal('120.00'), caucao_cotado=Decimal('500.00'),
        )
        Reserva.objects.create(
            cliente=self.c_logistica, grupo_veiculo=self.g_suv,
            veiculo=None, situacao='cancelada', canal='telefone',
            data_retirada=timezone.now() - timedelta(days=2),
            data_devolucao=timezone.now() + timedelta(days=5),
            diaria_cotada=Decimal('220.00'), caucao_cotado=Decimal('1200.00'),
        )

        self.stdout.write('    5 reservas criadas')

    # ─── Contratos (6) ────────────────────────────────────────────────────────

    def _criar_contratos(self):
        from apps.contracts.models import (
            Reserva, Contrato, AdicionalContrato, AvariaContrato,
            PagamentoContrato, ParcelaContrato,
        )
        from apps.financeiro.models import ContaReceber

        self.stdout.write('  Criando contratos...')
        agora = timezone.now()

        # ── C1: ATIVO (Carlos + Corolla, iniciou ha 5 dias) ──────────────────
        saida_1 = agora - timedelta(days=5)
        dev_prev_1 = agora + timedelta(days=9)
        res_1 = Reserva.objects.create(
            cliente=self.c_carlos, grupo_veiculo=self.g_sedan,
            veiculo=self.v_corolla, situacao='ativa', canal='balcao',
            data_retirada=saida_1, data_devolucao=dev_prev_1,
            diaria_cotada=Decimal('160.00'), caucao_cotado=Decimal('800.00'),
        )
        c1 = Contrato.objects.create(
            reserva=res_1, cliente=self.c_carlos, veiculo=self.v_corolla,
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
            descricao='Seguro basico diario', diaria=Decimal('15.00'), quantidade=1,
        )
        ParcelaContrato.objects.create(
            contrato=c1, numero=1, tipo='caucao',
            data_vencimento=saida_1.date(), valor=Decimal('800.00'),
            situacao='pago', data_pagamento=saida_1 + timedelta(minutes=30),
            forma_pagamento='pix', origem='original',
        )
        ParcelaContrato.objects.create(
            contrato=c1, numero=2, tipo='semanal',
            data_vencimento=saida_1.date() + timedelta(days=7),
            valor=Decimal('1120.00'), situacao='pendente', origem='original',
        )
        PagamentoContrato.objects.create(
            contrato=c1, forma_pagamento='pix', tipo='caucao',
            valor=Decimal('800.00'), data_pagamento=saida_1 + timedelta(minutes=30),
        )
        ContaReceber.objects.create(
            contrato=c1, cliente=self.c_carlos,
            descricao=f'Locacao - {c1.numero}',
            valor_total=Decimal('1960.00'), valor_pago=Decimal('0.00'),
            data_emissao=saida_1.date(), data_vencimento=dev_prev_1.date(),
            situacao='pendente',
        )

        # ── C2: ATIVO (Logistica + Compass, iniciou ha 12 dias) ───────────────
        saida_2 = agora - timedelta(days=12)
        dev_prev_2 = agora + timedelta(days=2)
        res_2 = Reserva.objects.create(
            cliente=self.c_logistica, grupo_veiculo=self.g_suv,
            veiculo=self.v_compass, situacao='ativa', canal='telefone',
            data_retirada=saida_2, data_devolucao=dev_prev_2,
            diaria_cotada=Decimal('220.00'), caucao_cotado=Decimal('1200.00'),
        )
        c2 = Contrato.objects.create(
            reserva=res_2, cliente=self.c_logistica, veiculo=self.v_compass,
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
        for i, (venc, sit) in enumerate([
            (saida_2.date(), 'pago'),
            (saida_2.date() + timedelta(days=7), 'pago'),
        ], start=1):
            kwargs = dict(
                contrato=c2, numero=i,
                tipo='caucao' if i == 1 else 'semanal',
                data_vencimento=venc, valor=Decimal('1200.00') if i == 1 else Decimal('1680.00'),
                situacao=sit, origem='original',
            )
            if sit == 'pago':
                kwargs['data_pagamento'] = timezone.make_aware(datetime.combine(venc, time(10, 0)))
                kwargs['forma_pagamento'] = 'cartao_credito'
            ParcelaContrato.objects.create(**kwargs)
        PagamentoContrato.objects.create(
            contrato=c2, forma_pagamento='cartao_credito', tipo='caucao',
            valor=Decimal('1200.00'), data_pagamento=saida_2 + timedelta(minutes=45),
        )
        PagamentoContrato.objects.create(
            contrato=c2, forma_pagamento='cartao_credito', tipo='locacao',
            valor=Decimal('1680.00'), data_pagamento=saida_2 + timedelta(days=7, hours=1),
        )
        ContaReceber.objects.create(
            contrato=c2, cliente=self.c_logistica,
            descricao=f'Locacao - {c2.numero}',
            valor_total=Decimal('3360.00'), valor_pago=Decimal('1680.00'),
            data_emissao=saida_2.date(), data_vencimento=dev_prev_2.date(),
            situacao='pago_parcial',
        )

        # ── C3: AGUARDANDO DEVOLUCAO (Pedro + Civic, bem atrasado) ────────────
        saida_3 = agora - timedelta(days=55)
        dev_prev_3 = agora - timedelta(days=40)  # ~40 dias em atraso
        res_3 = Reserva.objects.create(
            cliente=self.c_pedro, grupo_veiculo=self.g_sedan,
            veiculo=self.v_civic, situacao='ativa', canal='telefone',
            data_retirada=saida_3, data_devolucao=dev_prev_3,
            diaria_cotada=Decimal('160.00'), caucao_cotado=Decimal('800.00'),
        )
        c3 = Contrato.objects.create(
            reserva=res_3, cliente=self.c_pedro, veiculo=self.v_civic,
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
                kwargs['data_pagamento'] = timezone.make_aware(datetime.combine(venc, time(10, 0)))
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
            descricao=f'Locacao - {c3.numero}',
            valor_total=Decimal('3040.00'), valor_pago=Decimal('1920.00'),
            data_emissao=saida_3.date(), data_vencimento=dev_prev_3.date(),
            situacao='vencido',
        )

        # ── C4: ABERTO (Joao + Argo — checkout pendente) ──────────────────────
        c4 = Contrato.objects.create(
            cliente=self.c_joao, veiculo=self.v_argo,
            situacao='aberto', criado_por=self.admin_user,
            data_devolucao_prevista=agora + timedelta(days=10),
            diaria=Decimal('120.00'), km_franquia_diaria=200,
            valor_km_excedente=Decimal('1.50'),
            caucao_valor=Decimal('500.00'), caucao_situacao='pendente',
        )
        ContaReceber.objects.create(
            contrato=c4, cliente=self.c_joao,
            descricao=f'Locacao - {c4.numero}',
            valor_total=Decimal('1700.00'), valor_pago=Decimal('0.00'),
            data_emissao=agora.date(), data_vencimento=(agora + timedelta(days=10)).date(),
            situacao='pendente',
        )

        # ── C5: ENCERRADO PAGO (Maria + Yaris, 35 dias atras) ─────────────────
        saida_5 = agora - timedelta(days=35)
        dev_5 = agora - timedelta(days=28)
        res_5 = Reserva.objects.create(
            cliente=self.c_maria, grupo_veiculo=self.g_hatch,
            veiculo=self.v_yaris, situacao='concluida', canal='whatsapp',
            data_retirada=saida_5, data_devolucao=dev_5,
            diaria_cotada=Decimal('120.00'), caucao_cotado=Decimal('500.00'),
        )
        c5 = Contrato.objects.create(
            reserva=res_5, cliente=self.c_maria, veiculo=self.v_yaris,
            situacao='encerrado', criado_por=self.admin_user,
            data_saida=saida_5, km_saida=53500, combustivel_saida='cheio',
            data_devolucao_prevista=dev_5, data_devolucao_real=dev_5,
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
                data_pagamento=timezone.make_aware(datetime.combine(venc, time(14, 0))),
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
            observacoes='Pagamento de KM excedente — 300 km x R$1,50',
        )
        ContaReceber.objects.create(
            contrato=c5, cliente=self.c_maria,
            descricao=f'Locacao - {c5.numero}',
            valor_total=Decimal('1290.00'), valor_pago=Decimal('1290.00'),
            data_emissao=saida_5.date(), data_vencimento=dev_5.date(),
            situacao='pago',
        )

        # ── C6: ENCERRADO COM AVARIA E SALDO EM ABERTO (Ana + HR-V, ~100 dias atras) ──
        saida_6 = agora - timedelta(days=100)
        dev_6 = agora - timedelta(days=90)
        res_6 = Reserva.objects.create(
            cliente=self.c_ana, grupo_veiculo=self.g_suv,
            veiculo=self.v_hrv, situacao='concluida', canal='site',
            data_retirada=saida_6, data_devolucao=dev_6,
            diaria_cotada=Decimal('220.00'), caucao_cotado=Decimal('1200.00'),
        )
        c6 = Contrato.objects.create(
            reserva=res_6, cliente=self.c_ana, veiculo=self.v_hrv,
            situacao='encerrado', criado_por=self.admin_user,
            data_saida=saida_6, km_saida=10500, combustivel_saida='cheio',
            data_devolucao_prevista=dev_6, data_devolucao_real=dev_6,
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
            contrato=c6, descricao='Arranhao na porta traseira esquerda',
            localizacao='Porta traseira esquerda', valor_cobrado=Decimal('350.00'),
            situacao='identificada',
        )
        for i, venc in enumerate([saida_6.date(), saida_6.date() + timedelta(days=7)], start=1):
            ParcelaContrato.objects.create(
                contrato=c6, numero=i,
                tipo='caucao' if i == 1 else 'semanal',
                data_vencimento=venc,
                valor=Decimal('1200.00') if i == 1 else Decimal('1540.00'),
                situacao='pago',
                data_pagamento=timezone.make_aware(datetime.combine(venc, time(11, 0))),
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
        # Avaria da porta ainda nao paga -> saldo em aberto, bem vencido (bucket +90 dias)
        ContaReceber.objects.create(
            contrato=c6, cliente=self.c_ana,
            descricao=f'Locacao - {c6.numero}',
            valor_total=Decimal('3090.00'), valor_pago=Decimal('2740.00'),
            data_emissao=saida_6.date(), data_vencimento=dev_6.date(),
            situacao='vencido',
        )

        self.stdout.write(f'    {Contrato.objects.filter(pk__in=[c1.pk,c2.pk,c3.pk,c4.pk,c5.pk,c6.pk]).count()} contratos criados '
                          f'({ParcelaContrato.objects.filter(contrato__in=[c1,c2,c3,c5,c6]).count()} parcelas, '
                          f'{PagamentoContrato.objects.filter(contrato__in=[c1,c2,c3,c5,c6]).count()} pagamentos)')

    # ─── Financeiro ───────────────────────────────────────────────────────────

    def _criar_financeiro(self):
        from apps.financeiro.models import DespesaOperacional, MultaTransito, gerar_parcelas_despesa
        from apps.contracts.models import Contrato

        self.stdout.write('  Criando dados financeiros...')
        hoje = timezone.now().date()

        c1 = Contrato.objects.filter(veiculo=self.v_corolla, situacao='ativo').latest('criado_em')
        c3 = Contrato.objects.filter(veiculo=self.v_civic, situacao='aguardando_devolucao').latest('criado_em')

        despesas = [
            # Manutencao (nao inclui Gol/Argo — geradas automaticamente pelas OS concluidas)
            ('manutencao', 'Troca de embreagem e revisao geral — Cruze LT',
             self.v_cruze, Decimal('1850.00'), hoje - timedelta(days=3), hoje - timedelta(days=3)),
            # Seguro
            ('seguro', 'Renovacao seguro anual — Corolla 2023',
             self.v_corolla, Decimal('3200.00'), date(2026, 1, 15), date(2026, 1, 15)),
            ('seguro', 'Renovacao seguro anual — HR-V',
             self.v_hrv, Decimal('4100.00'), date(2026, 2, 5), date(2026, 2, 5)),
            # IPVA
            ('ipva', 'IPVA 2026 — Volkswagen Gol',
             self.v_gol, Decimal('1380.00'), date(2026, 3, 10), date(2026, 3, 10)),
            ('ipva', 'IPVA 2026 — Toyota Corolla',
             self.v_corolla, Decimal('4350.00'), date(2026, 3, 10), date(2026, 3, 10)),
            # Licenciamento
            ('licenciamento', 'CRLV 2026 — Honda HR-V',
             self.v_hrv, Decimal('98.00'), date(2026, 4, 1), date(2026, 4, 1)),
            # Combustivel
            ('combustivel', 'Abastecimento preparacao entrega — Argo',
             self.v_argo, Decimal('95.00'), hoje, None),
            ('combustivel', 'Abastecimento revisao — Yaris',
             self.v_yaris, Decimal('72.00'), hoje - timedelta(days=5), hoje - timedelta(days=5)),
            # Lavagem
            ('lavagem', 'Higienizacao completa + cera — HR-V',
             self.v_hrv, Decimal('280.00'), hoje - timedelta(days=1), hoje - timedelta(days=1)),
            ('lavagem', 'Lavagem simples pos-devolucao — Yaris',
             self.v_yaris, Decimal('45.00'), hoje - timedelta(days=28), hoje - timedelta(days=28)),
            # Administrativo (sem veiculo)
            ('salario', 'Salario atendente — mes atual',
             None, Decimal('2800.00'), hoje.replace(day=1), None),
            ('salario', 'Salario mecanico — mes atual',
             None, Decimal('3200.00'), hoje.replace(day=1), None),
            ('aluguel', 'Aluguel sede — mes atual',
             None, Decimal('5500.00'), hoje.replace(day=1), hoje.replace(day=5)),
            ('marketing', 'Impulsionamento redes sociais — mes atual',
             None, Decimal('800.00'), hoje - timedelta(days=10), hoje - timedelta(days=10)),
            ('outros', 'Material de escritorio e EPIs',
             None, Decimal('320.00'), hoje - timedelta(days=15), hoje - timedelta(days=15)),
        ]
        for cat, desc, veiculo, valor, comp, pag in despesas:
            DespesaOperacional.objects.create(
                categoria=cat, descricao=desc, veiculo=veiculo, valor=valor,
                data_competencia=comp, data_pagamento=pag,
                criado_por=self.admin_user,
            )

        # ── Despesas parceladas — alimentam "Contas a Pagar" com parcelas
        #    retroativas (pagas/atrasadas) e atuais/futuras (pendentes) ──
        ipva_kicks = DespesaOperacional.objects.create(
            categoria='ipva', descricao='IPVA 2026 parcelado — Nissan Kicks',
            veiculo=self.v_kicks, valor=Decimal('1750.00'),
            data_competencia=hoje - timedelta(days=120), parcelado=True, numero_parcelas=5,
            criado_por=self.admin_user,
        )
        gerar_parcelas_despesa(ipva_kicks)
        parcelas_kicks = list(ipva_kicks.parcelas.order_by('numero'))
        # 3 primeiras pagas, a 4a vencida sem pagamento (em_atraso), a 5a pendente futura
        for p in parcelas_kicks[:3]:
            p.situacao = 'pago'
            p.data_pagamento = p.data_vencimento
            p.forma_pagamento = 'boleto'
            p.save(update_fields=['situacao', 'data_pagamento', 'forma_pagamento'])
        if len(parcelas_kicks) >= 4:
            p4 = parcelas_kicks[3]
            p4.situacao = 'em_atraso'
            p4.save(update_fields=['situacao'])

        seguro_cruze = DespesaOperacional.objects.create(
            categoria='seguro', descricao='Seguro anual parcelado — Cruze LT (debito automatico)',
            veiculo=self.v_cruze, valor=Decimal('3600.00'),
            data_competencia=hoje - timedelta(days=60), parcelado=True, numero_parcelas=12,
            debito_automatico=True, forma_pagamento='cartao_credito',
            criado_por=self.admin_user,
        )
        gerar_parcelas_despesa(seguro_cruze)

        # Multas de transito
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
            data_infracao=hoje - timedelta(days=45),
            data_notificacao=hoje - timedelta(days=40),
            prazo_indicacao=hoje - timedelta(days=5),  # prazo ja vencido!
            descricao='Avanco de sinal vermelho em cruzamento',
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
            f'    {len(despesas) + 2} despesas (2 parceladas), '
            f'{MultaTransito.objects.filter(numero_auto__startswith="AIT-2026").count() + MultaTransito.objects.filter(numero_auto="AIT-2025-09981").count()} multas'
        )

    # ─── Manutencao ───────────────────────────────────────────────────────────

    def _criar_manutencao(self):
        from apps.manutencao.models import OrdemManutencao, AlertaManutencao
        from apps.contracts.models import Contrato

        self.stdout.write('  Criando manutencoes...')
        hoje = timezone.now().date()
        c1 = Contrato.objects.filter(veiculo=self.v_corolla, situacao='ativo').latest('criado_em')

        OrdemManutencao.objects.create(
            veiculo=self.v_cruze, tipo='corretiva', situacao='em_andamento',
            descricao='Troca de embreagem e revisao geral dos 40.000 km',
            km_na_manutencao=42000,
            data_entrada=hoje - timedelta(days=3),
            fornecedor='Auto Center SP — Unidade Santana',
            custo_total=Decimal('1850.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_kicks, tipo='corretiva', situacao='em_andamento',
            descricao='Reparo no sistema de arrefecimento',
            km_na_manutencao=38500,
            data_entrada=hoje - timedelta(days=2),
            fornecedor='Nissan Service Center',
            custo_total=Decimal('980.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_hrv, tipo='preventiva', situacao='agendada',
            descricao='Revisao 12.000 km — troca de oleo e filtros',
            km_na_manutencao=12000,
            data_agendada=hoje + timedelta(days=13),
            fornecedor='Concessionaria Honda Sao Paulo',
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_corolla, contrato=c1, tipo='sinistro', situacao='agendada',
            descricao='Reparo arranhao lateral direito — avaria durante locacao',
            km_na_manutencao=18900,
            data_agendada=hoje + timedelta(days=5),
            fornecedor='Funilaria Paulista',
            custo_total=Decimal('450.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_gol, tipo='preventiva', situacao='concluida',
            descricao='Troca de oleo 5W30 e filtro de ar — 25.000 km',
            km_na_manutencao=25000,
            data_entrada=hoje - timedelta(days=35), data_saida=hoje - timedelta(days=35),
            fornecedor='Troca Rapida Lubrificantes',
            custo_total=Decimal('185.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_argo, tipo='preventiva', situacao='concluida',
            descricao='Balanceamento, alinhamento e rodizio de pneus',
            km_na_manutencao=35000,
            data_entrada=hoje - timedelta(days=20), data_saida=hoje - timedelta(days=20),
            fornecedor='Pneus Brasil — Zona Norte',
            custo_total=Decimal('120.00'),
        )
        OrdemManutencao.objects.create(
            veiculo=self.v_yaris, tipo='preventiva', situacao='agendada',
            descricao='Revisao dos 55.000 km — troca pastilhas de freio e fluido',
            km_na_manutencao=55000,
            data_agendada=hoje + timedelta(days=20),
            fornecedor='Concessionaria Toyota Norte',
        )

        AlertaManutencao.objects.create(
            veiculo=self.v_corolla, tipo_alerta='km',
            descricao='Troca de oleo a cada 10.000 km',
            km_proximo_servico=25000, km_intervalo=10000, ativo=True,
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_gol, tipo_alerta='data',
            descricao='Revisao anual preventiva',
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
            data_proximo_servico=date(2026, 6, 5), ativo=True,  # proximo de vencer
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_yaris, tipo_alerta='km',
            descricao='Troca de oleo a cada 10.000 km',
            km_proximo_servico=55000, km_intervalo=10000, ativo=True,  # vencido!
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_compass, tipo_alerta='data',
            descricao='Revisao dos 6 meses na concessionaria',
            data_proximo_servico=date(2026, 3, 18), ativo=True,  # vencido!
        )
        AlertaManutencao.objects.create(
            veiculo=self.v_kicks, tipo_alerta='km',
            descricao='Revisao do sistema de arrefecimento a cada 40.000 km',
            km_proximo_servico=40000, km_intervalo=40000, ativo=True,  # proximo de vencer
        )

        self.stdout.write(
            f'    {OrdemManutencao.objects.filter(veiculo__in=[self.v_cruze,self.v_kicks,self.v_hrv,self.v_corolla,self.v_gol,self.v_argo,self.v_yaris]).count()} ordens, '
            f'{AlertaManutencao.objects.filter(veiculo__in=[self.v_corolla,self.v_gol,self.v_cruze,self.v_hrv,self.v_yaris,self.v_compass,self.v_kicks]).count()} alertas'
        )

    # ─── Investidores ─────────────────────────────────────────────────────────

    def _criar_investidores(self):
        from apps.investidores.models import Investidor, VeiculoInvestidor, CobrancaGestao

        self.stdout.write('  Criando investidores...')
        hoje = timezone.now().date()

        inv_ricardo = Investidor.objects.create(
            tipo='pf', nome='Ricardo Nogueira Investimentos',
            cpf='890.123.456-78', email='ricardo.nogueira@email.com',
            celular='(11) 99887-6655',
            dados_bancarios='Banco Inter — Ag 0001 — CC 123456-7 — PIX: ricardo.nogueira@email.com',
            situacao='ativo',
        )
        inv_capital = Investidor.objects.create(
            tipo='pj', nome='Capital Frotas', razao_social='Capital Frotas Participacoes LTDA',
            cnpj='23.456.789/0001-11', email='contato@capitalfrotas.com.br',
            telefone='(11) 3345-6677',
            dados_bancarios='Banco Bradesco — Ag 4321 — CC 998877-1',
            situacao='ativo',
        )

        vi_gol = VeiculoInvestidor.objects.create(
            investidor=inv_ricardo, veiculo=self.v_gol,
            taxa_gestao_semanal=Decimal('180.00'), dia_vencimento=10,
            data_inicio=hoje - timedelta(days=150), ativo=True,
        )
        vi_onix = VeiculoInvestidor.objects.create(
            investidor=inv_capital, veiculo=self.v_onix,
            taxa_gestao_semanal=Decimal('160.00'), dia_vencimento=15,
            data_inicio=hoje - timedelta(days=90), ativo=True,
        )

        # Cobrancas semanais retroativas + atual, com uma pendente e uma em atraso
        for semanas_atras in range(8, -1, -1):
            inicio = hoje - timedelta(weeks=semanas_atras, days=hoje.weekday())
            fim = inicio + timedelta(days=6)
            venc = fim + timedelta(days=3)
            pago = semanas_atras >= 2  # as duas mais recentes ficam em aberto
            CobrancaGestao.objects.create(
                veiculo_investidor=vi_gol, semana_inicio=inicio, semana_fim=fim,
                valor=Decimal('180.00'), data_vencimento=venc,
                situacao='pago' if pago else 'pendente',
                data_pagamento=venc if pago else None,
                forma_pagamento='pix' if pago else '',
            )

        for semanas_atras in range(6, -1, -1):
            inicio = hoje - timedelta(weeks=semanas_atras, days=hoje.weekday())
            fim = inicio + timedelta(days=6)
            venc = fim + timedelta(days=5)
            pago = semanas_atras >= 1
            CobrancaGestao.objects.create(
                veiculo_investidor=vi_onix, semana_inicio=inicio, semana_fim=fim,
                valor=Decimal('160.00'), data_vencimento=venc,
                situacao='pago' if pago else 'pendente',
                data_pagamento=venc if pago else None,
                forma_pagamento='transferencia' if pago else '',
            )

        self.stdout.write(
            f'    2 investidores, 2 vinculos, {CobrancaGestao.objects.filter(veiculo_investidor__in=[vi_gol, vi_onix]).count()} cobrancas'
        )

    # ─── Resumo ───────────────────────────────────────────────────────────────

    def _imprimir_resumo(self):
        from apps.fleet.models import Veiculo
        from apps.customers.models import Cliente
        from apps.contracts.models import Contrato
        from apps.financeiro.models import ContaReceber, DespesaOperacional, ParcelaDespesa, MultaTransito
        from apps.manutencao.models import OrdemManutencao, AlertaManutencao
        from apps.investidores.models import Investidor, CobrancaGestao

        W = self.stdout.write
        S = self.style.SUCCESS
        sep = '  ' + '=' * 53

        W('\n' + sep)
        W(S('  RESUMO (totais no banco, incluindo dados ja existentes)'))
        W(sep)
        W(f'  Usuario usado (criado_por) : {self.admin_user.username}')
        W(f'  Veiculos                   : {Veiculo.objects.count()}')
        W(f'  Clientes                   : {Cliente.objects.count()}')
        W(f'  Contratos                  : {Contrato.objects.count()}')
        W(f'  Contas a receber           : {ContaReceber.objects.count()}')
        W(f'  Despesas operacionais      : {DespesaOperacional.objects.count()}')
        W(f'  Parcelas de despesa        : {ParcelaDespesa.objects.count()}')
        W(f'  Multas de transito         : {MultaTransito.objects.count()}')
        W(f'  Ordens de manutencao       : {OrdemManutencao.objects.count()}')
        W(f'  Alertas preventivos        : {AlertaManutencao.objects.count()}')
        W(f'  Investidores                : {Investidor.objects.count()}')
        W(f'  Cobrancas de gestao        : {CobrancaGestao.objects.count()}')
        W(sep + '\n')
