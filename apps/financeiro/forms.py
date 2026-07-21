from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import ConfiguracaoLocadora, ContaReceber, DespesaOperacional, MultaTransito
from apps.contracts.models import PagamentoContrato
from apps.core.validators import validar_cpf

FC = 'form-control'
FS = 'form-select'


class ConfiguracaoLocadoraForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoLocadora
        fields = [
            'percentual_multa_atraso',
            'percentual_juros_diario',
            'dias_carencia',
            'custo_reposicao_combustivel',
        ]
        widgets = {
            'percentual_multa_atraso': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'percentual_juros_diario': forms.NumberInput(attrs={'class': FC, 'step': '0.0001', 'min': '0'}),
            'dias_carencia': forms.NumberInput(attrs={'class': FC, 'min': '0'}),
            'custo_reposicao_combustivel': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
        }
        help_texts = {
            'percentual_multa_atraso': 'Ex: 2.00 = 2% sobre o valor da parcela em atraso.',
            'percentual_juros_diario': 'Ex: 0.0333 = aprox. 1% ao mês.',
            'dias_carencia': 'Dias após o vencimento antes de aplicar multa e juros. 0 = sem carência.',
            'custo_reposicao_combustivel': 'Valor cobrado por cada 1/4 de tanque faltante. 0 = não cobrar.',
        }


class DespesaOperacionalForm(forms.ModelForm):
    # Campos de data declarados explicitamente para forçar formato ISO (YYYY-MM-DD),
    # necessário para inputs type="date" funcionarem corretamente com LANGUAGE_CODE=pt-br.
    data_competencia = forms.DateField(
        label='Data de competência',
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )
    data_pagamento = forms.DateField(
        label='Data de pagamento',
        required=False,
        widget=forms.DateInput(attrs={'class': FC, 'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )

    class Meta:
        model = DespesaOperacional
        fields = [
            'categoria', 'descricao', 'valor', 'data_competencia',
            'data_pagamento', 'parcelado', 'numero_parcelas', 'forma_pagamento',
            'debito_automatico', 'veiculo', 'observacoes',
        ]
        widgets = {
            'categoria': forms.Select(attrs={'class': FS}),
            'descricao': forms.TextInput(attrs={'class': FC}),
            'valor': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'parcelado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'numero_parcelas': forms.NumberInput(attrs={'class': FC, 'min': 2, 'max': 120}),
            'forma_pagamento': forms.Select(attrs={'class': FS}),
            'debito_automatico': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'veiculo': forms.Select(attrs={'class': FS}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    def clean(self):
        dados = super().clean()
        parcelado = dados.get('parcelado')
        numero_parcelas = dados.get('numero_parcelas')
        forma_pagamento = dados.get('forma_pagamento')

        if parcelado:
            if not numero_parcelas or numero_parcelas < 2:
                self.add_error('numero_parcelas', 'Informe o numero de parcelas (minimo 2).')
            if not forma_pagamento:
                self.add_error('forma_pagamento', 'Selecione a forma de pagamento parcelada.')
            # Cartão de crédito parcelado = débito automático implícito: as parcelas
            # são debitadas automaticamente na fatura todo mês.
            if forma_pagamento == 'cartao_credito':
                dados['debito_automatico'] = True
        return dados


class MultaTransitoForm(forms.ModelForm):
    class Meta:
        model = MultaTransito
        fields = ['veiculo', 'numero_auto', 'data_infracao', 'data_notificacao',
                  'prazo_indicacao', 'descricao', 'pontos', 'valor',
                  'condutor_nome', 'condutor_cpf', 'situacao', 'observacoes']
        widgets = {
            'veiculo': forms.Select(attrs={'class': FS}),
            'numero_auto': forms.TextInput(attrs={'class': FC}),
            'data_infracao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'data_notificacao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'prazo_indicacao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'descricao': forms.Textarea(attrs={'class': FC, 'rows': 2}),
            'pontos': forms.NumberInput(attrs={'class': FC, 'min': 0}),
            'valor': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'condutor_nome': forms.TextInput(attrs={'class': FC}),
            'condutor_cpf': forms.TextInput(attrs={'class': FC, 'placeholder': '000.000.000-00'}),
            'situacao': forms.Select(attrs={'class': FS}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    def clean_condutor_cpf(self):
        cpf = self.cleaned_data.get('condutor_cpf', '').strip()
        if cpf:
            try:
                validar_cpf(cpf)
            except ValidationError as e:
                raise forms.ValidationError(e.message)
        return cpf


class RecebimentoForm(forms.Form):
    """Form para registrar um pagamento contra uma ContaReceber."""
    forma_pagamento = forms.ChoiceField(
        label='Forma de Pagamento',
        choices=PagamentoContrato.FORMA,
        widget=forms.Select(attrs={'class': FS})
    )
    tipo = forms.ChoiceField(
        label='Referente a',
        choices=PagamentoContrato.TIPO,
        initial='locacao',
        widget=forms.Select(attrs={'class': FS})
    )
    valor = forms.DecimalField(
        label='Valor (R$)',
        min_value=Decimal('0.01'),
        max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': FC, 'step': '0.01'})
    )
    data_pagamento = forms.DateTimeField(
        label='Data/hora',
        initial=timezone.now,
        widget=forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'})
    )
    observacoes = forms.CharField(
        label='Observações',
        required=False,
        widget=forms.Textarea(attrs={'class': FC, 'rows': 2})
    )


class ContaReceberFiltroForm(forms.Form):
    situacao = forms.ChoiceField(
        label='Situação',
        choices=[('', 'Todas')] + ContaReceber.SITUACAO,
        required=False,
        widget=forms.Select(attrs={'class': FS + ' form-select-sm'})
    )
    busca = forms.CharField(
        label='Busca',
        required=False,
        widget=forms.TextInput(attrs={'class': FC + ' form-control-sm', 'placeholder': 'Cliente ou contrato…'})
    )
    vencimento_de = forms.DateField(
        label='Vencimento de',
        required=False,
        widget=forms.DateInput(attrs={'class': FC + ' form-control-sm', 'type': 'date'})
    )
    vencimento_ate = forms.DateField(
        label='até',
        required=False,
        widget=forms.DateInput(attrs={'class': FC + ' form-control-sm', 'type': 'date'})
    )
