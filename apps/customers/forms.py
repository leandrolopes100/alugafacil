import re
from django import forms
from django.core.exceptions import ValidationError

from apps.core.validators import validar_cpf, validar_cnpj
from .models import Cliente, CNHCliente

FC = 'form-control'
FS = 'form-select'


def _so_digitos(v):
    return re.sub(r'\D', '', str(v or ''))


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            'tipo', 'nome', 'cpf', 'data_nascimento',
            'razao_social', 'cnpj', 'contato',
            'email', 'telefone', 'celular',
            'logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'estado', 'cep',
            'situacao', 'motivo_bloqueio', 'observacoes',
        ]
        widgets = {
            'tipo': forms.Select(attrs={'class': FS, 'x-model': 'tipo'}),
            'nome': forms.TextInput(attrs={'class': FC}),
            'cpf': forms.TextInput(attrs={'class': FC, 'placeholder': '000.000.000-00'}),
            'data_nascimento': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'razao_social': forms.TextInput(attrs={'class': FC}),
            'cnpj': forms.TextInput(attrs={'class': FC, 'placeholder': '00.000.000/0000-00'}),
            'contato': forms.TextInput(attrs={'class': FC}),
            'email': forms.EmailInput(attrs={'class': FC}),
            'telefone': forms.TextInput(attrs={'class': FC}),
            'celular': forms.TextInput(attrs={'class': FC}),
            'logradouro': forms.TextInput(attrs={'class': FC}),
            'numero': forms.TextInput(attrs={'class': FC}),
            'complemento': forms.TextInput(attrs={'class': FC}),
            'bairro': forms.TextInput(attrs={'class': FC}),
            'cidade': forms.TextInput(attrs={'class': FC}),
            'estado': forms.TextInput(attrs={'class': FC, 'maxlength': 2, 'style': 'text-transform:uppercase'}),
            'cep': forms.TextInput(attrs={'class': FC, 'placeholder': '00000-000'}),
            'situacao': forms.Select(attrs={'class': FS}),
            'motivo_bloqueio': forms.Textarea(attrs={'class': FC, 'rows': 2}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 3}),
        }

    def clean_cpf(self):
        cpf = self.cleaned_data.get('cpf', '').strip()
        tipo = self.cleaned_data.get('tipo') or self.data.get('tipo')
        if tipo == 'pf' and cpf:
            try:
                validar_cpf(cpf)
            except ValidationError as e:
                raise forms.ValidationError(e.message)
            # Verifica unicidade
            qs = Cliente.objects.filter(cpf=cpf)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError('Já existe um cliente cadastrado com este CPF.')
        return cpf

    def clean_cnpj(self):
        cnpj = self.cleaned_data.get('cnpj', '').strip()
        tipo = self.cleaned_data.get('tipo') or self.data.get('tipo')
        if tipo == 'pj' and cnpj:
            try:
                validar_cnpj(cnpj)
            except ValidationError as e:
                raise forms.ValidationError(e.message)
            qs = Cliente.objects.filter(cnpj=cnpj)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError('Já existe um cliente cadastrado com este CNPJ.')
        return cnpj

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get('tipo')
        nome = cleaned.get('nome', '').strip()
        razao_social = cleaned.get('razao_social', '').strip()
        cpf = cleaned.get('cpf', '').strip()
        cnpj = cleaned.get('cnpj', '').strip()

        if tipo == 'pf' and not nome:
            self.add_error('nome', 'Nome completo é obrigatório para Pessoa Física.')
        if tipo == 'pf' and not cpf:
            self.add_error('cpf', 'CPF é obrigatório para Pessoa Física.')
        if tipo == 'pj' and not razao_social:
            self.add_error('razao_social', 'Razão Social é obrigatória para Pessoa Jurídica.')
        if tipo == 'pj' and not cnpj:
            self.add_error('cnpj', 'CNPJ é obrigatório para Pessoa Jurídica.')
        return cleaned


class CNHClienteForm(forms.ModelForm):
    class Meta:
        model = CNHCliente
        fields = ['numero', 'estado_emissor', 'categoria', 'validade',
                  'primeira_habilitacao', 'foto_frente', 'foto_verso', 'principal']
        widgets = {
            'numero': forms.TextInput(attrs={'class': FC}),
            'estado_emissor': forms.TextInput(attrs={'class': FC, 'maxlength': 2}),
            'categoria': forms.Select(attrs={'class': FS}),
            'validade': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'primeira_habilitacao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'foto_frente': forms.FileInput(attrs={'class': FC}),
            'foto_verso': forms.FileInput(attrs={'class': FC}),
            'principal': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
