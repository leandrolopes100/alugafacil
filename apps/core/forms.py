from django.contrib.auth.forms import AuthenticationForm


class FormularioLogin(AuthenticationForm):
    error_messages = {
        'invalid_login': 'Usuário ou senha incorretos. Confira as letras maiúsculas e tente de novo.',
        'inactive': 'Este acesso está desativado. Peça ao administrador para reativá-lo.',
    }
