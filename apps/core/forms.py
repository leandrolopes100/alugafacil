from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm

from .models import PerfilAcesso
from .permissoes import DEPENDENCIAS, MODULOS, PERMISSOES, faltando_dependencia


class FormularioLogin(AuthenticationForm):
    error_messages = {
        'invalid_login': 'Usuário ou senha incorretos. Confira as letras maiúsculas e tente de novo.',
        'inactive': 'Este acesso está desativado. Peça ao administrador para reativá-lo.',
    }


def _estilizar(form):
    for campo in form.fields.values():
        widget = campo.widget
        if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
            classe = 'form-check-input'
        elif isinstance(widget, forms.Select):
            classe = 'form-select'
        else:
            classe = 'form-control'
        widget.attrs.setdefault('class', classe)


class PerfilAcessoForm(forms.ModelForm):
    permissoes = forms.MultipleChoiceField(
        choices=list(PERMISSOES.items()), required=False, widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = PerfilAcesso
        fields = ['nome', 'descricao']
        widgets = {
            'nome': forms.TextInput(attrs={'maxlength': 80, 'autofocus': True}),
            'descricao': forms.TextInput(attrs={'maxlength': 200}),
        }

    def __init__(self, *args, copiar_de=None, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self)
        if self.instance.pk:
            self.initial['permissoes'] = sorted(self.instance.codigos)
        elif copiar_de:
            self.initial['nome'] = PerfilAcesso.nome_livre(f'Cópia de {copiar_de.nome}')
            self.initial['descricao'] = copiar_de.descricao
            self.initial['permissoes'] = sorted(copiar_de.codigos)

    def clean_nome(self):
        nome = ' '.join(self.cleaned_data['nome'].split())
        if not nome:
            raise forms.ValidationError('Informe o nome do perfil.')
        repetido = PerfilAcesso.objects.filter(nome__iexact=nome)
        if self.instance.pk:
            repetido = repetido.exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError('Já existe um perfil com este nome.')
        return nome

    def clean_permissoes(self):
        selecionadas = self.cleaned_data['permissoes']
        problemas = faltando_dependencia(selecionadas)
        if problemas:
            raise forms.ValidationError([
                f'"{PERMISSOES[codigo]}" exige também "{PERMISSOES[requisito]}".'
                for codigo, requisito in problemas
            ])
        return selecionadas

    def modulos(self):
        if self.is_bound:
            marcadas = set(self.data.getlist(self.add_prefix('permissoes')))
        else:
            marcadas = set(self.initial.get('permissoes', []))
        return [
            {
                'nome': nome,
                'icone': icone,
                'itens': [
                    {
                        'codigo': codigo,
                        'rotulo': rotulo,
                        'marcada': codigo in marcadas,
                        'requer': DEPENDENCIAS.get(codigo, ''),
                    }
                    for codigo, rotulo in itens
                ],
            }
            for nome, icone, itens in MODULOS
        ]


class UsuarioForm(forms.ModelForm):
    perfil = forms.ModelChoiceField(
        queryset=PerfilAcesso.objects.all(), empty_label='Selecione o perfil',
        error_messages={'required': 'Escolha o perfil de acesso.'},
    )
    senha1 = forms.CharField(label='Senha', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    senha2 = forms.CharField(label='Confirme a senha', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    class Meta:
        model = get_user_model()
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        labels = {
            'username': 'Usuário (login)',
            'first_name': 'Nome',
            'last_name': 'Sobrenome',
            'email': 'E-mail',
            'is_active': 'Acesso ativo',
        }
        help_texts = {
            'username': 'Letras, números e @ . + - _ (sem espaços). É o que a pessoa digita para entrar.',
        }

    def __init__(self, *args, editor, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        _estilizar(self)
        self.fields['username'].widget.attrs.update({'autocomplete': 'off', 'autocapitalize': 'none'})
        if self.instance.pk:
            del self.fields['senha1']
            del self.fields['senha2']
            perfis = [g.perfil for g in self.instance.groups.select_related('perfil') if hasattr(g, 'perfil')]
            self.perfis_atuais = perfis
            if perfis:
                self.initial['perfil'] = perfis[0].pk
            if self.instance.is_superuser:
                self.fields['perfil'].required = False
                self.fields['perfil'].empty_label = 'Nenhum (superusuário já tem acesso total)'
        else:
            self.perfis_atuais = []
            self.initial['is_active'] = True

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        repetido = get_user_model().objects.filter(username__iexact=username)
        if self.instance.pk:
            repetido = repetido.exclude(pk=self.instance.pk)
        if repetido.exists():
            raise forms.ValidationError('Já existe um usuário com este login.')
        return username

    def clean_is_active(self):
        ativo = self.cleaned_data['is_active']
        if not ativo and self.instance.pk and self.instance.pk == self.editor.pk:
            raise forms.ValidationError('Você não pode desativar o seu próprio usuário.')
        return ativo

    def clean(self):
        dados = super().clean()
        senha1, senha2 = dados.get('senha1'), dados.get('senha2')
        if 'senha1' in self.fields and senha1 and senha2:
            if senha1 != senha2:
                self.add_error('senha2', 'As senhas não conferem.')
            else:
                for campo in ('username', 'first_name', 'last_name', 'email'):
                    if campo in dados:
                        setattr(self.instance, campo, dados[campo])
                try:
                    password_validation.validate_password(senha1, self.instance)
                except forms.ValidationError as erro:
                    self.add_error('senha1', erro)
        return dados

    def salvar(self):
        usuario = super().save(commit=False)
        if 'senha1' in self.fields:
            usuario.set_password(self.cleaned_data['senha1'])
        usuario.save()
        perfil = self.cleaned_data.get('perfil')
        usuario.groups.set([perfil.grupo] if perfil else [])
        return usuario


class RedefinirSenhaForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_password1'].label = 'Nova senha'
        self.fields['new_password2'].label = 'Confirme a nova senha'
        _estilizar(self)
