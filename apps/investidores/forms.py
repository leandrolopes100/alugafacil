from django import forms
from django.utils import timezone

from apps.fleet.models import Veiculo
from .models import CobrancaGestao, Investidor, VeiculoInvestidor

FC = 'form-control'
FS = 'form-select'


class InvestidorForm(forms.ModelForm):
    class Meta:
        model = Investidor
        fields = [
            'tipo', 'nome', 'cpf', 'razao_social', 'cnpj',
            'email', 'telefone', 'celular',
            'dados_bancarios', 'observacoes', 'situacao',
        ]
        widgets = {
            'tipo': forms.Select(attrs={'class': FS}),
            'nome': forms.TextInput(attrs={'class': FC}),
            'cpf': forms.TextInput(attrs={'class': FC, 'placeholder': '000.000.000-00'}),
            'razao_social': forms.TextInput(attrs={'class': FC}),
            'cnpj': forms.TextInput(attrs={'class': FC, 'placeholder': '00.000.000/0000-00'}),
            'email': forms.EmailInput(attrs={'class': FC}),
            'telefone': forms.TextInput(attrs={'class': FC, 'placeholder': '(00) 0000-0000'}),
            'celular': forms.TextInput(attrs={'class': FC, 'placeholder': '(00) 00000-0000'}),
            'dados_bancarios': forms.Textarea(attrs={'class': FC, 'rows': 3}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
            'situacao': forms.Select(attrs={'class': FS}),
        }


class VincularVeiculoForm(forms.ModelForm):
    veiculo = forms.ModelChoiceField(
        label='Veículo',
        queryset=Veiculo.objects.none(),
        widget=forms.Select(attrs={'class': FS}),
    )
    data_inicio = forms.DateField(
        label='Data de início',
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )

    class Meta:
        model = VeiculoInvestidor
        fields = ['veiculo', 'taxa_gestao_semanal', 'dia_vencimento', 'data_inicio', 'observacoes']
        widgets = {
            'taxa_gestao_semanal': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'dia_vencimento': forms.NumberInput(attrs={'class': FC, 'min': '1', 'max': '28'}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ja_vinculados = VeiculoInvestidor.objects.filter(
            ativo=True
        ).values_list('veiculo_id', flat=True)
        self.fields['veiculo'].queryset = (
            Veiculo.objects.exclude(pk__in=ja_vinculados)
            .exclude(situacao='inativo')
            .order_by('placa')
        )

    def clean_dia_vencimento(self):
        dia = self.cleaned_data.get('dia_vencimento')
        if dia is None or not (1 <= dia <= 28):
            raise forms.ValidationError('Informe um dia entre 1 e 28.')
        return dia


class GerarCobrancaForm(forms.ModelForm):
    semana_inicio = forms.DateField(
        label='Início da semana',
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )
    semana_fim = forms.DateField(
        label='Fim da semana',
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )
    data_vencimento = forms.DateField(
        label='Data de vencimento',
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )

    class Meta:
        model = CobrancaGestao
        fields = ['semana_inicio', 'semana_fim', 'valor', 'data_vencimento', 'observacoes']
        widgets = {
            'valor': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    def clean(self):
        dados = super().clean()
        inicio = dados.get('semana_inicio')
        fim = dados.get('semana_fim')
        if inicio and fim and fim < inicio:
            self.add_error('semana_fim', 'A data de fim deve ser posterior à data de início.')
        return dados


class GerarCobrancaLoteForm(forms.Form):
    semana_inicio = forms.DateField(
        label='Início da semana',
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )
    semana_fim = forms.DateField(
        label='Fim da semana',
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )

    def clean(self):
        dados = super().clean()
        inicio = dados.get('semana_inicio')
        fim = dados.get('semana_fim')
        if inicio and fim and fim < inicio:
            self.add_error('semana_fim', 'A data de fim deve ser posterior à data de início.')
        return dados


class PagarCobrancaForm(forms.Form):
    forma_pagamento = forms.ChoiceField(
        label='Forma de pagamento',
        choices=CobrancaGestao.FORMA_PAGAMENTO,
        widget=forms.Select(attrs={'class': FS}),
    )
    data_pagamento = forms.DateField(
        label='Data do recebimento',
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        initial=timezone.now,
    )
    observacoes = forms.CharField(
        label='Observações',
        required=False,
        widget=forms.Textarea(attrs={'class': FC, 'rows': 2}),
    )
