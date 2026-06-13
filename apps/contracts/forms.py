from django import forms
from django.utils import timezone

from .models import AdicionalContrato, AvariaContrato, Contrato, PagamentoContrato, Reserva
from apps.fleet.models import Veiculo

FC = 'form-control'
FS = 'form-select'


class ReservaForm(forms.ModelForm):
    class Meta:
        model = Reserva
        fields = ['cliente', 'grupo_veiculo', 'veiculo', 'canal', 'data_retirada',
                  'data_devolucao', 'diaria_cotada', 'caucao_cotado', 'observacoes']
        widgets = {
            'cliente': forms.Select(attrs={'class': FS}),
            'grupo_veiculo': forms.Select(attrs={'class': FS}),
            'veiculo': forms.Select(attrs={'class': FS}),
            'canal': forms.Select(attrs={'class': FS}),
            'data_retirada': forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'}),
            'data_devolucao': forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'}),
            'diaria_cotada': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'caucao_cotado': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Veiculo.objects.filter(situacao='disponivel')
        # Inclui o veículo já vinculado à reserva mesmo que não esteja 'disponivel'
        if self.instance and self.instance.veiculo_id:
            qs = (qs | Veiculo.objects.filter(pk=self.instance.veiculo_id)).distinct()
        self.fields['veiculo'].queryset = qs.order_by('marca', 'modelo')
        self.fields['veiculo'].empty_label = 'Selecionar depois (opcional)'

    def clean(self):
        cleaned = super().clean()
        data_retirada = cleaned.get('data_retirada')
        data_devolucao = cleaned.get('data_devolucao')

        if data_retirada and data_devolucao:
            if data_devolucao <= data_retirada:
                raise forms.ValidationError(
                    'A data de devolução deve ser posterior à data de retirada.'
                )
            delta = data_devolucao - data_retirada
            if delta.days < 1:
                raise forms.ValidationError('A reserva deve ter no mínimo 1 dia de duração.')

        veiculo = cleaned.get('veiculo')
        if veiculo and veiculo.situacao not in ('disponivel', 'reservado'):
            raise forms.ValidationError(
                f'O veículo {veiculo.placa} não está disponível para reserva '
                f'(situação atual: {veiculo.get_situacao_display()}).'
            )
        return cleaned


class ContratoForm(forms.ModelForm):
    class Meta:
        model = Contrato
        fields = ['cliente', 'veiculo', 'data_devolucao_prevista',
                  'diaria', 'km_franquia_diaria', 'valor_km_excedente',
                  'caucao_valor', 'caucao_situacao']
        widgets = {
            'cliente': forms.Select(attrs={'class': FS}),
            'veiculo': forms.Select(attrs={'class': FS}),  # select renderizado manualmente no template
            'data_devolucao_prevista': forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'}),
            'diaria': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'km_franquia_diaria': forms.NumberInput(attrs={'class': FC}),
            'valor_km_excedente': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'caucao_valor': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'caucao_situacao': forms.Select(attrs={'class': FS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Inclui todos os veículos no queryset para que a validação server-side
        # funcione mesmo em POSTs diretos; o filtro visual é feito no template.
        self.fields['veiculo'].queryset = Veiculo.objects.select_related('grupo').order_by('marca', 'modelo')

    def clean_veiculo(self):
        veiculo = self.cleaned_data.get('veiculo')
        if not veiculo:
            return veiculo
        if not self.instance.pk and veiculo.situacao not in ('disponivel', 'reservado'):
            raise forms.ValidationError(
                f'O veículo {veiculo.placa} não está disponível para locação '
                f'(situação atual: {veiculo.get_situacao_display()}).'
            )
        return veiculo

    def clean_data_devolucao_prevista(self):
        data = self.cleaned_data.get('data_devolucao_prevista')
        if data and data <= timezone.now():
            raise forms.ValidationError(
                'A data de devolução prevista deve ser uma data/hora futura.'
            )
        return data

    def clean_diaria(self):
        diaria = self.cleaned_data.get('diaria')
        if diaria is not None and diaria <= 0:
            raise forms.ValidationError('O valor da diária deve ser maior que zero.')
        return diaria

    def clean_caucao_valor(self):
        caucao = self.cleaned_data.get('caucao_valor')
        if caucao is not None and caucao < 0:
            raise forms.ValidationError('O valor do caução não pode ser negativo.')
        return caucao


class CheckoutForm(forms.ModelForm):
    class Meta:
        model = Contrato
        fields = ['km_saida', 'combustivel_saida', 'obs_saida']
        widgets = {
            'km_saida': forms.NumberInput(attrs={'class': FC}),
            'combustivel_saida': forms.Select(attrs={'class': FS}),
            'obs_saida': forms.Textarea(attrs={'class': FC, 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['combustivel_saida'].required = True

    def clean_km_saida(self):
        km = self.cleaned_data.get('km_saida')
        if km is not None and km < 0:
            raise forms.ValidationError('A quilometragem não pode ser negativa.')
        return km


class CheckinForm(forms.ModelForm):
    class Meta:
        model = Contrato
        fields = ['data_devolucao_real', 'km_devolucao', 'combustivel_devolucao', 'obs_devolucao']
        widgets = {
            'data_devolucao_real': forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'}),
            'km_devolucao': forms.NumberInput(attrs={'class': FC}),
            'combustivel_devolucao': forms.Select(attrs={'class': FS}),
            'obs_devolucao': forms.Textarea(attrs={'class': FC, 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['data_devolucao_real'].required = True
        self.fields['combustivel_devolucao'].required = True
        if not self.initial.get('data_devolucao_real') and not (
            self.instance and self.instance.pk and self.instance.data_devolucao_real
        ):
            self.initial['data_devolucao_real'] = timezone.localtime(timezone.now()).strftime('%Y-%m-%dT%H:%M')

    def clean_data_devolucao_real(self):
        dt = self.cleaned_data.get('data_devolucao_real')
        if dt and timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt

    def clean_km_devolucao(self):
        km = self.cleaned_data.get('km_devolucao')
        if km is None:
            raise forms.ValidationError('Informe a quilometragem de devolução.')
        if self.instance and self.instance.km_saida and km < self.instance.km_saida:
            raise forms.ValidationError(
                f'A KM de devolução ({km}) não pode ser menor que a KM de saída '
                f'({self.instance.km_saida}).'
            )
        return km


class AdicionalContratoForm(forms.ModelForm):
    class Meta:
        model = AdicionalContrato
        fields = ['tipo', 'descricao', 'diaria', 'quantidade']
        widgets = {
            'tipo': forms.Select(attrs={'class': FS}),
            'descricao': forms.TextInput(attrs={'class': FC}),
            'diaria': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'quantidade': forms.NumberInput(attrs={'class': FC, 'min': 1}),
        }


class AvariaContratoForm(forms.ModelForm):
    class Meta:
        model = AvariaContrato
        fields = ['descricao', 'localizacao', 'valor_cobrado', 'foto', 'situacao']
        widgets = {
            'descricao': forms.TextInput(attrs={'class': FC}),
            'localizacao': forms.TextInput(attrs={'class': FC}),
            'valor_cobrado': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'foto': forms.FileInput(attrs={'class': FC}),
            'situacao': forms.Select(attrs={'class': FS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 'paga' só pode ser definido via ContratoAvariaMarcarPagaView (cria PagamentoContrato junto)
        self.fields['situacao'].choices = [
            c for c in AvariaContrato.SITUACAO if c[0] != 'paga'
        ]


class PagamentoContratoForm(forms.ModelForm):
    class Meta:
        model = PagamentoContrato
        fields = ['forma_pagamento', 'tipo', 'valor', 'data_pagamento', 'observacoes']
        widgets = {
            'forma_pagamento': forms.Select(attrs={'class': FS}),
            'tipo': forms.Select(attrs={'class': FS}),
            'valor': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0.01'}),
            'data_pagamento': forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    def clean_valor(self):
        valor = self.cleaned_data.get('valor')
        if valor is not None and valor <= 0:
            raise forms.ValidationError('O valor do pagamento deve ser maior que zero.')
        return valor
