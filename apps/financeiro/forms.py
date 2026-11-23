import re
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from .models import ConfiguracaoLocadora, ContaReceber, DespesaOperacional, MultaTransito
from apps.contracts.models import Contrato, PagamentoContrato
from apps.core.validators import validar_cnpj, validar_cpf

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
            'tolerancia_devolucao_minutos',
            'pix_tipo_chave',
            'pix_chave',
            'pix_nome_recebedor',
            'pix_cidade_recebedor',
            'whatsapp_locadora',
            'usar_modulo_investidores',
        ]
        widgets = {
            'percentual_multa_atraso': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'percentual_juros_diario': forms.NumberInput(attrs={'class': FC, 'step': '0.0001', 'min': '0'}),
            'dias_carencia': forms.NumberInput(attrs={'class': FC, 'min': '0'}),
            'custo_reposicao_combustivel': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'tolerancia_devolucao_minutos': forms.NumberInput(attrs={'class': FC, 'min': '0', 'max': '720'}),
            'pix_tipo_chave': forms.Select(attrs={'class': FS}),
            'pix_chave': forms.TextInput(attrs={'class': FC}),
            'pix_nome_recebedor': forms.TextInput(attrs={'class': FC, 'maxlength': '25'}),
            'pix_cidade_recebedor': forms.TextInput(attrs={'class': FC, 'maxlength': '15'}),
            'whatsapp_locadora': forms.TextInput(attrs={'class': FC, 'placeholder': '(11) 99999-9999'}),
            'usar_modulo_investidores': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'percentual_multa_atraso': 'Ex: 2.00 = 2% sobre o valor da parcela em atraso.',
            'percentual_juros_diario': 'Ex: 0.0333 = aprox. 1% ao mês.',
            'dias_carencia': 'Dias após o vencimento antes de aplicar multa e juros. 0 = sem carência.',
            'custo_reposicao_combustivel': 'Valor cobrado por cada 1/4 de tanque faltante. 0 = não cobrar.',
            'tolerancia_devolucao_minutos': 'Atraso na devolução que não gera diária extra. Ex: 60 = 1 hora. 0 = sem tolerância.',
            'pix_chave': 'Exatamente como está cadastrada no banco.',
        }

    def clean_tolerancia_devolucao_minutos(self):
        minutos = self.cleaned_data.get('tolerancia_devolucao_minutos')
        if minutos is not None and minutos > 720:
            raise ValidationError('A tolerância não pode passar de 720 minutos (12 horas).')
        return minutos

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pix_tipo_chave'].choices = [('', 'Selecione')] + ConfiguracaoLocadora.TIPO_CHAVE_PIX

    @property
    def campos_pix(self):
        return [self[nome] for nome in (
            'pix_tipo_chave', 'pix_chave', 'pix_nome_recebedor', 'pix_cidade_recebedor', 'whatsapp_locadora',
        )]

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get('pix_tipo_chave')
        chave = (cleaned.get('pix_chave') or '').strip()
        campos_pix = [chave, cleaned.get('pix_nome_recebedor'), cleaned.get('pix_cidade_recebedor')]

        if not tipo and not any(campos_pix):
            return cleaned
        if not tipo:
            self.add_error('pix_tipo_chave', 'Informe o tipo da chave Pix.')
            return cleaned
        for campo in ('pix_chave', 'pix_nome_recebedor', 'pix_cidade_recebedor'):
            if not (cleaned.get(campo) or '').strip():
                self.add_error(campo, 'Obrigatório para gerar o Pix das cobranças.')
        if not chave:
            return cleaned

        try:
            cleaned['pix_chave'] = self._normalizar_chave(tipo, chave)
        except ValidationError as erro:
            self.add_error('pix_chave', erro)
        return cleaned

    @staticmethod
    def _normalizar_chave(tipo, chave):
        digitos = re.sub(r'\D', '', chave)
        if tipo == 'cpf':
            validar_cpf(digitos)
            return digitos
        if tipo == 'cnpj':
            validar_cnpj(digitos)
            return digitos
        if tipo == 'email':
            validate_email(chave)
            return chave.lower()
        if tipo == 'telefone':
            if len(digitos) in (12, 13) and digitos.startswith('55'):
                digitos = digitos[2:]
            if len(digitos) not in (10, 11):
                raise ValidationError('Telefone inválido. Informe DDD + número.')
            return f'+55{digitos}'
        if not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', chave):
            raise ValidationError('Chave aleatória inválida. Copie do app do banco (formato com hífens).')
        return chave.lower()


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
            'valor': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0.01'}),
            'parcelado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'numero_parcelas': forms.NumberInput(attrs={'class': FC, 'min': 2, 'max': 120}),
            'forma_pagamento': forms.Select(attrs={'class': FS}),
            'debito_automatico': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'veiculo': forms.Select(attrs={'class': FS}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categoria'].widget.attrs['data-categorias-veiculo'] = ','.join(DespesaOperacional.CATEGORIAS_DE_VEICULO)

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
                  'condutor_nome', 'condutor_cpf', 'situacao', 'contrato', 'observacoes']
        widgets = {
            'veiculo': forms.Select(attrs={'class': FS}),
            'numero_auto': forms.TextInput(attrs={'class': FC}),
            'data_infracao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'data_notificacao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'prazo_indicacao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'descricao': forms.Textarea(attrs={'class': FC, 'rows': 2}),
            'pontos': forms.NumberInput(attrs={'class': FC, 'min': 0}),
            'valor': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0.01'}),
            'condutor_nome': forms.TextInput(attrs={'class': FC}),
            'condutor_cpf': forms.TextInput(attrs={'class': FC, 'placeholder': '000.000.000-00'}),
            'situacao': forms.Select(attrs={'class': FS}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    contrato = forms.ModelChoiceField(
        label='Contrato', queryset=Contrato.objects.none(), required=False,
        empty_label='Selecione o contrato',
        widget=forms.Select(attrs={'class': FS}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields['situacao']
        else:
            self.fields['situacao'].choices = [
                c for c in MultaTransito.SITUACAO if c[0] != 'cobrada_cliente'
            ]
        if self.instance.parcela_id:
            del self.fields['contrato']
            return
        campo = self.fields['contrato']
        campo.queryset = self._contratos_possiveis()
        campo.label_from_instance = self._rotulo_contrato
        campo.initial = self.instance.contrato_id

    def _valor_bruto(self, nome):
        if self.is_bound:
            valor = self.data.get(self.add_prefix(nome))
            try:
                return self.fields[nome].to_python(valor)
            except forms.ValidationError:
                return None
        return getattr(self.instance, f'{nome}_id' if nome == 'veiculo' else nome, None)

    def _contratos_possiveis(self):
        veiculo = self._valor_bruto('veiculo')
        veiculo_id = getattr(veiculo, 'pk', veiculo)
        possiveis = MultaTransito.contratos_possiveis(veiculo_id, self._valor_bruto('data_infracao'))
        if self.instance.contrato_id and self.instance.contrato.veiculo_id == veiculo_id:
            possiveis = possiveis | Contrato.objects.filter(pk=self.instance.contrato_id)
        return possiveis.select_related('cliente').order_by('data_saida').distinct()

    @staticmethod
    def _rotulo_contrato(contrato):
        saida = timezone.localtime(contrato.data_saida).strftime('%d/%m/%Y %H:%M') if contrato.data_saida else '—'
        if contrato.data_devolucao_real:
            devolucao = timezone.localtime(contrato.data_devolucao_real).strftime('%d/%m/%Y %H:%M')
        else:
            devolucao = 'com o cliente'
        return f'{contrato.numero} — {contrato.cliente} (saída {saida}, devolução {devolucao})'

    @property
    def contratos_em_duvida(self):
        return 'contrato' in self.fields and self.fields['contrato'].queryset.count() > 1

    def clean(self):
        dados = super().clean()
        if 'contrato' not in self.fields or dados.get('contrato'):
            return dados
        possiveis = list(MultaTransito.contratos_possiveis(
            getattr(dados.get('veiculo'), 'pk', None), dados.get('data_infracao'),
        ))
        if len(possiveis) > 1:
            self.add_error(
                'contrato',
                f'{len(possiveis)} contratos usaram este veículo nesse dia '
                f'({", ".join(c.numero for c in possiveis)}). Escolha de qual cliente cobrar.',
            )
        return dados

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
        choices=PagamentoContrato.FORMAS_RECEBIMENTO,
        widget=forms.Select(attrs={'class': FS})
    )
    tipo = forms.ChoiceField(
        label='Referente a',
        choices=[c for c in PagamentoContrato.TIPO if c[0] in Contrato.TIPOS_RECEBIMENTO],
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

    def clean_data_pagamento(self):
        data = self.cleaned_data.get('data_pagamento')
        if data and data > timezone.now() + timedelta(minutes=5):
            raise ValidationError('O pagamento não pode ter data futura.')
        return data


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
