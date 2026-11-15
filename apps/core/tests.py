from django.test import SimpleTestCase, TestCase

from .utils import numero_whatsapp


class NumeroWhatsappTests(SimpleTestCase):

    def test_celular_com_mascara(self):
        self.assertEqual(numero_whatsapp('(11) 98765-4321'), '5511987654321')

    def test_celular_ja_com_codigo_do_pais(self):
        self.assertEqual(numero_whatsapp('+55 11 98765-4321'), '5511987654321')

    def test_celular_antigo_sem_nono_digito_recebe_o_9(self):
        self.assertEqual(numero_whatsapp('11 8765-4321'), '5511987654321')

    def test_telefone_fixo_e_recusado(self):
        self.assertIsNone(numero_whatsapp('(11) 3456-7890'))

    def test_vazio_ou_incompleto_e_recusado(self):
        self.assertIsNone(numero_whatsapp(''))
        self.assertIsNone(numero_whatsapp(None))
        self.assertIsNone(numero_whatsapp('98765-4321'))



class TelaLoginTests(TestCase):

    def setUp(self):
        from django.contrib.auth.models import User
        self.usuario = User.objects.create_user('atendente.demo', password='Senha#123')

    def entrar(self, usuario, senha, **extra):
        from django.urls import reverse
        return self.client.post(reverse('login'), {'username': usuario, 'password': senha, **extra})

    def test_tela_renderiza_com_favicon_e_campos_acessiveis(self):
        from django.urls import reverse
        resposta = self.client.get(reverse('login'))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'favicon-32.png')
        self.assertContains(resposta, 'autocomplete="current-password"')
        self.assertContains(resposta, 'aria-label="Mostrar senha"')
        self.assertContains(resposta, 'Aluga <span class="marca-destaque">Fácil</span>')

    def test_senha_errada_mostra_erro_claro_e_mantem_o_usuario(self):
        resposta = self.entrar('atendente.demo', 'errada')
        self.assertContains(resposta, 'Usuário ou senha incorretos')
        self.assertContains(resposta, 'value="atendente.demo"')
        self.assertContains(resposta, 'aria-invalid="true"')
        self.assertContains(resposta, 'role="alert"')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_conta_desativada_tem_mensagem_propria(self):
        from django.contrib.auth.models import User
        from django.test import override_settings
        self.usuario.is_active = False
        self.usuario.save()
        with override_settings(AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.AllowAllUsersModelBackend']):
            resposta = self.entrar('atendente.demo', 'Senha#123')
        self.assertContains(resposta, 'Este acesso está desativado')

    def test_campos_vazios_sem_javascript_mostram_aviso(self):
        resposta = self.entrar('', '')
        self.assertContains(resposta, 'Preencha o usuário e a senha para entrar.')

    def test_login_correto_vai_para_o_next(self):
        resposta = self.entrar('atendente.demo', 'Senha#123', next='/contratos/')
        self.assertRedirects(resposta, '/contratos/', fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)

    def test_next_externo_e_ignorado(self):
        resposta = self.entrar('atendente.demo', 'Senha#123', next='https://site-malicioso.com/')
        self.assertRedirects(resposta, '/', fetch_redirect_response=False)

    def test_usuario_ja_logado_vai_direto_para_o_painel(self):
        from django.urls import reverse
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse('login'))
        self.assertRedirects(resposta, '/', fetch_redirect_response=False)

    def test_aviso_de_continuar_quando_vem_de_pagina_protegida(self):
        resposta = self.client.get('/login/?next=/financeiro/')
        self.assertContains(resposta, 'Entre para continuar de onde parou.')


class PeriodoInvalidoTests(TestCase):

    URLS = [
        ('financeiro:fluxo', []),
        ('core:relatorio-dre', []),
        ('core:relatorio-contratos', []),
        ('core:relatorio-frota', []),
        ('core:relatorio-clientes', []),
        ('core:relatorio-exportar', ['contratos']),
        ('core:relatorio-exportar', ['frota']),
    ]
    PARAMETROS = [
        {'mes': '13'}, {'mes': '0'}, {'mes': 'abc'}, {'ano': 'x'},
        {'ano': '99999'}, {'ano': '-1'}, {'ano': '2026', 'mes': '-3'},
    ]

    def setUp(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser('gestor_periodo', 'p@teste.com', 'senha123'))

    def test_mes_ou_ano_invalido_volta_para_o_periodo_atual_sem_erro(self):
        from django.urls import reverse
        for nome, args in self.URLS:
            for parametros in self.PARAMETROS:
                with self.subTest(url=nome, args=args, parametros=parametros):
                    self.assertEqual(self.client.get(reverse(nome, args=args), parametros).status_code, 200)

    def test_leitura_do_periodo(self):
        from datetime import date
        from .utils import ler_ano_mes
        hoje = date(2026, 11, 15)
        self.assertEqual(ler_ano_mes({'ano': '2025', 'mes': '3'}, hoje, 11), (2025, 3))
        self.assertEqual(ler_ano_mes({'mes': '13'}, hoje, 11), (2026, 11))
        self.assertEqual(ler_ano_mes({'ano': 'x'}, hoje, 0), (2026, 0))
        self.assertEqual(ler_ano_mes({'ano': '2025', 'mes': ''}, hoje, 11), (2025, 0))
        self.assertEqual(ler_ano_mes({'ano': '1999'}, hoje, 11), (2026, 11))
