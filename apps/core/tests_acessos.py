from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import URLResolver, get_resolver, reverse
from django.views import View

from .acessos import AcessoInvalido, administradores_ativos, validar_administracao
from .mixins import PermissaoRequeridaMixin
from .models import PerfilAcesso
from .permissoes import DEPENDENCIAS, PERFIS_PADRAO, PERMISSOES, tem_permissao

A, T, F, M = 'admin_locadora', 'atendente', 'financeiro', 'mecanico'

ACESSO_ANTERIOR = {
    'ReservaListView': {A, T}, 'ReservaCreateView': {A, T}, 'ReservaUpdateView': {A, T},
    'ReservaDeleteView': {A}, 'ReservaDetailView': {A, T}, 'ReservaConfirmarView': {A, T},
    'ReservaCancelarView': {A, T}, 'ReservaNoShowView': {A, T}, 'ReservaConverterView': {A, T},
    'ContratoListView': {A, T, F}, 'ContratoCreateView': {A, T}, 'ContratoDetailView': {A, T, F},
    'ContratoCheckoutView': {A, T}, 'ContratoCheckinView': {A, T}, 'ContratoEncerrarView': {A, T},
    'ContratoEncerramentoAntecipadoView': {A}, 'ContratoReverterCheckinView': {A},
    'ContratoCancelarView': {A, T}, 'ContratoProrrogarView': {A, T}, 'PagarParcelaView': {A, T, F},
    'ContratoAdicionaisView': {A, T}, 'ContratoAvariasView': {A, T}, 'ContratoAvariaMarcarPagaView': {A, T, F},
    'ContratoAvariaAjustarView': {A, T, F}, 'ContratoAdicionalRemoverView': {A, T},
    'ContratoPDFView': {A, T, F}, 'ContratoPagamentosView': {A, T, F}, 'ContratoPagamentoEstornarView': {A},
    'RelatoriosIndexView': {A, T, F}, 'RelatorioContratosView': {A, T, F}, 'RelatorioFrotaView': {A, T, M},
    'RelatorioFinanceiroView': {A, F}, 'RelatorioClientesView': {A, T}, 'RelatorioInadimplenciaView': {A, F},
    'ExportarCSVView': {A, F},
    'ClienteListView': {A, T}, 'ClienteCreateView': {A, T}, 'ClienteDetailView': {A, T},
    'ClienteUpdateView': {A, T}, 'ClienteCNHView': {A, T}, 'ClienteCNHExcluirView': {A},
    'ConfiguracaoLocadoraView': {A}, 'AgendaCobrancasView': {A, F}, 'RegistrarCobrancaWhatsappView': {A, F},
    'AgendaPagamentosView': {A, F}, 'IndexFinanceiroView': {A, F}, 'ContaReceberListView': {A, F},
    'ContaReceberDetailView': {A, F}, 'ReceberPagamentoView': {A, F}, 'DespesaListView': {A, F},
    'DespesaCreateView': {A, F}, 'DespesaDetailView': {A, F}, 'ParcelaDespesaPagarView': {A, F},
    'ParcelaDespesaEstornarView': {A, F}, 'ParcelaDespesaEstornarLoteView': {A, F}, 'DespesaDeleteView': {A},
    'DespesaUpdateView': {A, F}, 'DespesaMarcarPagoView': {A, F}, 'DespesaDesmarcarPagoView': {A, F},
    'ParcelaDespesaPagarLoteView': {A, F}, 'ContasPagarListView': {A, F}, 'FluxoCaixaView': {A, F},
    'MultaListView': {A, F}, 'MultaCreateView': {A, F}, 'MultaDetailView': {A, F},
    'MultaAlterarSituacaoView': {A, F}, 'MultaUpdateView': {A, F},
    'VeiculoListView': {A, T, M}, 'VeiculoCreateView': {A, T, M}, 'VeiculoDetailView': {A, T, M},
    'VeiculoAlterarSituacaoView': {A, T, M}, 'VeiculoDeleteView': {A}, 'VeiculoUpdateView': {A, T, M},
    'VeiculoFotosView': {A, T, M}, 'VeiculoFotoExcluirView': {A}, 'VeiculoDocumentosView': {A, T, M},
    'VeiculoDocumentoExcluirView': {A}, 'GrupoListView': {A, T, M}, 'GrupoCreateView': {A},
    'GrupoUpdateView': {A}, 'GrupoTarifasSimularView': {A}, 'CategoriaListView': {A, T, M},
    'CategoriaCreateView': {A}, 'CategoriaUpdateView': {A}, 'DisponibilidadeView': {A, T, M},
    'VeiculoTarifasView': {A, T, M},
    'InvestidorListView': {A, F}, 'InvestidorCreateView': {A, F}, 'InvestidorDetalheView': {A, F},
    'InvestidorEditarView': {A, F}, 'VincularVeiculoView': {A, F}, 'DesvincularVeiculoView': {A, F},
    'CobrancaListView': {A, F}, 'GerarCobrancaView': {A, F}, 'GerarCobrancaLoteView': {A, F},
    'PagarCobrancaView': {A, F}, 'CancelarCobrancaView': {A, F},
    'ManutencaoListView': {A, M}, 'ManutencaoCreateView': {A, M}, 'ManutencaoDetailView': {A, M},
    'ManutencaoUpdateView': {A, M}, 'ManutencaoAlterarStatusView': {A, M}, 'AlertaListView': {A, M},
    'AlertaCreateView': {A, M},
}

TELAS_NOVAS_DE_ADMINISTRACAO = {
    'PerfilListView', 'PerfilCreateView', 'PerfilUpdateView', 'PerfilDeleteView',
    'UsuarioListView', 'UsuarioCreateView', 'UsuarioUpdateView', 'UsuarioSenhaView',
}

SEM_PERMISSAO_POR_DESENHO = {
    ('apps.core.views', 'DashboardView'), ('apps.core.views', 'BuscaGlobalView'),
    ('apps.contracts.views', 'AssinaturaView'), ('apps.financeiro.views', 'CobrancaPublicaView'),
    ('django.contrib.auth.views', 'LoginView'), ('django.contrib.auth.views', 'LogoutView'),
    ('django.views.generic.base', 'RedirectView'),
}


def _views_das_urls():
    def andar(padroes):
        for padrao in padroes:
            if isinstance(padrao, URLResolver):
                yield from andar(padrao.url_patterns)
            else:
                yield padrao.callback
    return [getattr(cb, 'view_class', None) or cb for cb in andar(get_resolver().url_patterns)]


def usuario_do_grupo(nome_grupo, login=None):
    usuario = User.objects.create_user(login or f'u_{nome_grupo}', password='Senha-forte-123')
    usuario.groups.add(Group.objects.get(name=nome_grupo))
    return usuario


class EquivalenciaComOsGruposAntigosTests(TestCase):

    def test_cada_tela_libera_exatamente_os_mesmos_grupos_de_antes(self):
        usuarios = {grupo: usuario_do_grupo(grupo) for grupo in (A, T, F, M)}
        conferidas = set()
        for view in _views_das_urls():
            if not (isinstance(view, type) and issubclass(view, PermissaoRequeridaMixin)):
                continue
            nome = view.__name__
            if nome in TELAS_NOVAS_DE_ADMINISTRACAO:
                continue
            self.assertIn(nome, ACESSO_ANTERIOR, f'{nome} é nova: decida quem acessa e inclua na tabela')
            for grupo, usuario in usuarios.items():
                usuario = User.objects.get(pk=usuario.pk)
                self.assertEqual(
                    tem_permissao(usuario, view.permissao), grupo in ACESSO_ANTERIOR[nome],
                    f'{nome} mudou para o grupo {grupo}',
                )
            conferidas.add(nome)
        self.assertEqual(conferidas, set(ACESSO_ANTERIOR))

    def test_toda_tela_com_url_exige_permissao_ou_e_publica_por_desenho(self):
        for view in _views_das_urls():
            if not isinstance(view, type):
                self.assertTrue(view.__module__.startswith(('django.contrib.admin', 'django.contrib.auth.admin',
                                                            'django.contrib.contenttypes', 'django.views.static')),
                                f'view em função sem controle: {view.__module__}.{view.__name__}')
                continue
            if issubclass(view, PermissaoRequeridaMixin):
                codigos_validos = (view.permissao,) if isinstance(view.permissao, str) else view.permissao
                self.assertTrue(set(codigos_validos) <= set(PERMISSOES), view.__name__)
                continue
            self.assertIn((view.__module__, view.__name__), SEM_PERMISSAO_POR_DESENHO,
                          f'{view.__name__} não exige permissão')

    def test_telas_de_usuarios_e_perfis_so_para_quem_administra_acessos(self):
        for grupo in (T, F, M):
            usuario = usuario_do_grupo(grupo)
            self.assertFalse(tem_permissao(usuario, 'admin_usuarios'))
        self.assertTrue(tem_permissao(usuario_do_grupo(A), 'admin_usuarios'))

    def test_superusuario_passa_em_tudo_e_usuario_desativado_em_nada(self):
        superusuario = User.objects.create_superuser('raiz', 'r@x.com', 'Senha-forte-123')
        desativado = usuario_do_grupo(A, 'desligado')
        desativado.is_active = False
        desativado.save()
        desativado = User.objects.get(pk=desativado.pk)
        for codigo in PERMISSOES:
            self.assertTrue(tem_permissao(superusuario, codigo))
            self.assertFalse(tem_permissao(desativado, codigo))

    def test_http_atendente_barrado_no_financeiro_e_financeiro_entra(self):
        self.client.force_login(usuario_do_grupo(T))
        resposta = self.client.get(reverse('financeiro:index'))
        self.assertRedirects(resposta, reverse('core:dashboard'), fetch_redirect_response=False)
        self.client.force_login(usuario_do_grupo(F))
        self.assertEqual(self.client.get(reverse('financeiro:index')).status_code, 200)

    def test_anonimo_vai_para_o_login(self):
        resposta = self.client.get(reverse('financeiro:index'))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn('/login/', resposta['Location'])


class CatalogoEMigracaoTests(TestCase):

    def test_todas_as_permissoes_do_catalogo_existem_no_banco(self):
        no_banco = set(PerfilAcesso.permissoes_do_sistema().values_list('codename', flat=True))
        self.assertEqual(no_banco, set(PERMISSOES))

    def test_migracao_criou_os_4_perfis_com_as_permissoes_do_catalogo(self):
        for nome_grupo, (nome, _, codigos) in PERFIS_PADRAO.items():
            perfil = Group.objects.get(name=nome_grupo).perfil
            self.assertEqual(perfil.nome, nome)
            self.assertEqual(perfil.codigos, set(codigos))

    def test_perfis_padrao_respeitam_as_dependencias(self):
        for nome, _, codigos in PERFIS_PADRAO.values():
            for codigo in codigos:
                if codigo in DEPENDENCIAS:
                    self.assertIn(DEPENDENCIAS[codigo], codigos, f'{nome}: {codigo}')

    def test_grupo_criado_por_fora_ganha_perfil(self):
        grupo = Group.objects.create(name='Criado no admin')
        self.assertEqual(grupo.perfil.nome, 'Criado no admin')
        self.assertEqual(grupo.perfil.codigos, set())

    def test_criar_grupos_nao_desfaz_ajuste_feito_na_tela_e_redefinir_desfaz(self):
        perfil = Group.objects.get(name=T).perfil
        perfil.definir_permissoes({'clientes_acessar'})
        call_command('criar_grupos', stdout=StringIO())
        self.assertEqual(perfil.codigos, {'clientes_acessar'})
        call_command('criar_grupos', '--redefinir', stdout=StringIO())
        self.assertEqual(perfil.codigos, set(PERFIS_PADRAO[T][2]))

    def test_definir_permissoes_preserva_permissoes_de_modelo_do_django(self):
        from django.contrib.auth.models import Permission
        grupo = Group.objects.get(name=F)
        de_modelo = Permission.objects.exclude(content_type__app_label='core').first()
        grupo.permissions.add(de_modelo)
        grupo.perfil.definir_permissoes({'financeiro_acessar'})
        self.assertTrue(grupo.permissions.filter(pk=de_modelo.pk).exists())


class MixinFechadoPorPadraoTests(TestCase):

    def _chamar(self, permissao):
        class Tela(PermissaoRequeridaMixin, View):
            def get(self, request):
                return None
        Tela.permissao = permissao
        requisicao = RequestFactory().get('/')
        requisicao.user = User.objects.create_superuser(f'su{User.objects.count()}', 'a@a.com', 'x')
        return Tela.as_view()(requisicao)

    def test_view_sem_permissao_declarada_quebra(self):
        with self.assertRaises(ImproperlyConfigured):
            self._chamar(None)

    def test_permissao_com_erro_de_digitacao_quebra(self):
        with self.assertRaises(ImproperlyConfigured):
            self._chamar('contratos_opera')


class TelaDePerfisTests(TestCase):

    def setUp(self):
        self.admin = usuario_do_grupo(A, 'gestor')
        self.client.force_login(self.admin)

    def test_cria_perfil_com_permissoes_e_usuario_passa_a_ter_acesso(self):
        resposta = self.client.post(reverse('core:perfil-novo'), {
            'nome': 'Recepção', 'descricao': 'Só clientes',
            'permissoes': ['clientes_acessar'],
        })
        self.assertRedirects(resposta, reverse('core:perfis'))
        perfil = PerfilAcesso.objects.get(nome='Recepção')
        self.assertEqual(perfil.codigos, {'clientes_acessar'})
        recepcionista = User.objects.create_user('rec', password='Senha-forte-123')
        recepcionista.groups.add(perfil.grupo)
        self.client.force_login(recepcionista)
        self.assertEqual(self.client.get(reverse('clientes:lista')).status_code, 200)
        self.assertEqual(self.client.get(reverse('contratos:lista')).status_code, 302)

    def test_recusa_permissao_sem_a_dependencia(self):
        resposta = self.client.post(reverse('core:perfil-novo'), {
            'nome': 'Quebrado', 'permissoes': ['frota_excluir'],
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'exige também')
        self.assertFalse(PerfilAcesso.objects.filter(nome='Quebrado').exists())

    def test_recusa_nome_repetido_sem_diferenciar_maiusculas(self):
        resposta = self.client.post(reverse('core:perfil-novo'), {'nome': 'ATENDENTE', 'permissoes': []})
        self.assertContains(resposta, 'Já existe um perfil com este nome.')

    def test_permissao_desconhecida_no_post_e_recusada(self):
        resposta = self.client.post(reverse('core:perfil-novo'), {'nome': 'X', 'permissoes': ['superpoder']})
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(PerfilAcesso.objects.filter(nome='X').exists())

    def test_alterar_perfil_vale_na_hora_para_os_usuarios_dele(self):
        mecanico = usuario_do_grupo(M)
        perfil = Group.objects.get(name=M).perfil
        self.client.post(reverse('core:perfil-editar', args=[perfil.pk]), {
            'nome': perfil.nome, 'permissoes': ['frota_acessar', 'manutencao_acessar', 'financeiro_acessar'],
        })
        self.assertTrue(tem_permissao(User.objects.get(pk=mecanico.pk), 'financeiro_acessar'))

    def test_nao_retira_o_proprio_acesso_a_usuarios_e_nada_muda(self):
        perfil = Group.objects.get(name=A).perfil
        resposta = self.client.post(reverse('core:perfil-editar', args=[perfil.pk]), {
            'nome': 'Administrador', 'permissoes': ['clientes_acessar'],
        })
        self.assertContains(resposta, 'Você não pode retirar o seu próprio acesso')
        self.assertEqual(perfil.codigos, set(PERMISSOES))

    def test_excluir_perfil_com_usuario_e_bloqueado(self):
        usuario_do_grupo(M)
        perfil = Group.objects.get(name=M).perfil
        self.client.post(reverse('core:perfil-excluir', args=[perfil.pk]))
        self.assertTrue(PerfilAcesso.objects.filter(pk=perfil.pk).exists())

    def test_excluir_perfil_sem_usuarios_apaga_o_grupo(self):
        perfil = PerfilAcesso.criar('Temporário')
        grupo_pk = perfil.grupo_id
        self.client.post(reverse('core:perfil-excluir', args=[perfil.pk]))
        self.assertFalse(Group.objects.filter(pk=grupo_pk).exists())
        self.assertFalse(PerfilAcesso.objects.filter(pk=perfil.pk).exists())

    def test_duplicar_preenche_com_as_permissoes_da_origem(self):
        perfil = Group.objects.get(name=F).perfil
        resposta = self.client.get(reverse('core:perfil-novo'), {'copiar': perfil.pk})
        self.assertEqual(resposta.context['form'].initial['nome'], 'Cópia de Financeiro')
        self.assertEqual(set(resposta.context['form'].initial['permissoes']), perfil.codigos)

    def test_atendente_nao_abre_perfis_nem_usuarios(self):
        self.client.force_login(usuario_do_grupo(T))
        for nome in ('core:perfis', 'core:usuarios', 'core:perfil-novo', 'core:usuario-novo'):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 302, nome)
        self.client.post(reverse('core:perfil-novo'), {'nome': 'Invasor', 'permissoes': list(PERMISSOES)})
        self.assertFalse(PerfilAcesso.objects.filter(nome='Invasor').exists())


class TelaDeUsuariosTests(TestCase):

    def setUp(self):
        self.admin = usuario_do_grupo(A, 'gestor')
        self.client.force_login(self.admin)
        self.perfil_mecanico = Group.objects.get(name=M).perfil

    def dados(self, **extra):
        base = {
            'username': 'joao', 'first_name': 'João', 'last_name': 'Silva', 'email': '',
            'is_active': 'on', 'perfil': self.perfil_mecanico.pk,
            'senha1': 'Carro-azul-2026', 'senha2': 'Carro-azul-2026',
        }
        base.update(extra)
        return base

    def test_cria_usuario_que_consegue_entrar_e_so_ve_o_do_perfil(self):
        resposta = self.client.post(reverse('core:usuario-novo'), self.dados())
        self.assertRedirects(resposta, reverse('core:usuarios'))
        joao = User.objects.get(username='joao')
        self.assertFalse(joao.is_superuser)
        self.assertFalse(joao.is_staff)
        self.assertEqual(list(joao.groups.all()), [self.perfil_mecanico.grupo])
        self.client.logout()
        self.assertTrue(self.client.login(username='joao', password='Carro-azul-2026'))
        pagina = self.client.get(reverse('core:dashboard'))
        self.assertContains(pagina, 'Ordens de Serviço')
        self.assertNotContains(pagina, 'Painel Financeiro')
        self.assertNotContains(pagina, reverse('core:usuarios'))

    def test_senha_fraca_ou_diferente_e_recusada(self):
        resposta = self.client.post(reverse('core:usuario-novo'), self.dados(senha1='123', senha2='123'))
        self.assertEqual(resposta.status_code, 200)
        resposta = self.client.post(reverse('core:usuario-novo'), self.dados(senha2='Outra-senha-2026'))
        self.assertContains(resposta, 'As senhas não conferem.')
        self.assertFalse(User.objects.filter(username='joao').exists())

    def test_login_repetido_sem_diferenciar_maiusculas(self):
        resposta = self.client.post(reverse('core:usuario-novo'), self.dados(username='GESTOR'))
        self.assertContains(resposta, 'Já existe um usuário com este login.')

    def test_perfil_e_obrigatorio(self):
        resposta = self.client.post(reverse('core:usuario-novo'), self.dados(perfil=''))
        self.assertContains(resposta, 'Escolha o perfil de acesso.')

    def test_trocar_perfil_deixa_um_so(self):
        joao = usuario_do_grupo(T, 'joao')
        joao.groups.add(Group.objects.get(name=F))
        self.client.post(reverse('core:usuario-editar', args=[joao.pk]), {
            'username': 'joao', 'first_name': '', 'last_name': '', 'email': '',
            'is_active': 'on', 'perfil': self.perfil_mecanico.pk,
        })
        self.assertEqual(list(joao.groups.all()), [self.perfil_mecanico.grupo])

    def test_nao_desativa_a_si_mesmo(self):
        resposta = self.client.post(reverse('core:usuario-editar', args=[self.admin.pk]), {
            'username': 'gestor', 'perfil': Group.objects.get(name=A).perfil.pk,
        })
        self.assertContains(resposta, 'Você não pode desativar o seu próprio usuário.')
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_active)

    def test_nao_tira_de_si_o_perfil_de_administrador(self):
        resposta = self.client.post(reverse('core:usuario-editar', args=[self.admin.pk]), {
            'username': 'gestor', 'is_active': 'on', 'perfil': self.perfil_mecanico.pk,
        })
        self.assertContains(resposta, 'Você não pode retirar o seu próprio acesso')
        self.assertTrue(self.admin.groups.filter(name=A).exists())

    def test_desativar_derruba_a_sessao_aberta(self):
        joao = usuario_do_grupo(T, 'joao')
        outro = self.client_class()
        outro.force_login(joao)
        self.assertEqual(outro.get(reverse('clientes:lista')).status_code, 200)
        self.client.post(reverse('core:usuario-editar', args=[joao.pk]), {
            'username': 'joao', 'perfil': Group.objects.get(name=T).perfil.pk,
        })
        self.assertFalse(User.objects.get(pk=joao.pk).is_active)
        self.assertIn('/login/', outro.get(reverse('clientes:lista'))['Location'])

    def test_admin_comum_nao_altera_superusuario(self):
        raiz = User.objects.create_superuser('raiz', 'r@x.com', 'Senha-forte-123')
        for nome in ('core:usuario-editar', 'core:usuario-senha'):
            self.assertRedirects(self.client.get(reverse(nome, args=[raiz.pk])), reverse('core:usuarios'))
        self.client.post(reverse('core:usuario-senha', args=[raiz.pk]), {
            'new_password1': 'Tomada-de-conta-1', 'new_password2': 'Tomada-de-conta-1',
        })
        self.assertTrue(User.objects.get(pk=raiz.pk).check_password('Senha-forte-123'))

    def test_redefinir_senha_de_outro_derruba_a_sessao_dele(self):
        joao = usuario_do_grupo(T, 'joao')
        outro = self.client_class()
        outro.force_login(joao)
        resposta = self.client.post(reverse('core:usuario-senha', args=[joao.pk]), {
            'new_password1': 'Nova-senha-forte-9', 'new_password2': 'Nova-senha-forte-9',
        })
        self.assertRedirects(resposta, reverse('core:usuarios'))
        self.assertTrue(User.objects.get(pk=joao.pk).check_password('Nova-senha-forte-9'))
        self.assertIn('/login/', outro.get(reverse('clientes:lista'))['Location'])

    def test_redefinir_a_propria_senha_mantem_a_sessao(self):
        self.client.post(reverse('core:usuario-senha', args=[self.admin.pk]), {
            'new_password1': 'Nova-senha-forte-9', 'new_password2': 'Nova-senha-forte-9',
        })
        self.assertEqual(self.client.get(reverse('core:usuarios')).status_code, 200)

    def test_lista_mostra_perfil_e_situacao(self):
        usuario_do_grupo(M, 'oficina')
        resposta = self.client.get(reverse('core:usuarios'))
        self.assertContains(resposta, 'oficina')
        self.assertContains(resposta, 'Mecânico')


class InvarianteDeAdministracaoTests(TestCase):

    def test_sem_nenhum_administrador_ativo_e_recusado(self):
        gestor = usuario_do_grupo(A, 'gestor')
        self.assertTrue(administradores_ativos().filter(pk=gestor.pk).exists())
        User.objects.filter(pk=gestor.pk).update(is_active=False)
        self.assertFalse(administradores_ativos().exists())
        with self.assertRaisesMessage(AcessoInvalido, 'pelo menos um usuário ativo'):
            validar_administracao(User(is_superuser=True))

    def test_administrador_desativado_nao_conta(self):
        gestor = usuario_do_grupo(A, 'gestor')
        outro = usuario_do_grupo(A, 'outro')
        User.objects.filter(pk=outro.pk).update(is_active=False)
        self.assertEqual(list(administradores_ativos()), [gestor])

    def test_superusuario_ativo_conta_como_administrador(self):
        raiz = User.objects.create_superuser('raiz', 'r@x.com', 'Senha-forte-123')
        validar_administracao(raiz)
