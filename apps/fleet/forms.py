from django import forms
from .models import CategoriaVeiculo, DocumentoVeiculo, FotoVeiculo, GrupoVeiculo, Veiculo

FC = 'form-control'
FS = 'form-select'
FC_SM = 'form-control form-control-sm'


class VeiculoForm(forms.ModelForm):
    class Meta:
        model = Veiculo
        fields = [
            'placa', 'chassi', 'renavam', 'marca', 'modelo',
            'ano_fabricacao', 'ano_modelo', 'cor', 'combustivel',
            'transmissao', 'portas', 'lugares', 'km_atual', 'grupo',
            'situacao', 'data_aquisicao', 'valor_aquisicao', 'valor_fipe',
        ]
        widgets = {
            'placa': forms.TextInput(attrs={'class': FC, 'placeholder': 'ABC-1D23', 'style': 'text-transform:uppercase'}),
            'chassi': forms.TextInput(attrs={'class': FC}),
            'renavam': forms.TextInput(attrs={'class': FC}),
            'marca': forms.TextInput(attrs={'class': FC}),
            'modelo': forms.TextInput(attrs={'class': FC}),
            'ano_fabricacao': forms.NumberInput(attrs={'class': FC}),
            'ano_modelo': forms.NumberInput(attrs={'class': FC}),
            'cor': forms.TextInput(attrs={'class': FC}),
            'combustivel': forms.Select(attrs={'class': FS}),
            'transmissao': forms.Select(attrs={'class': FS}),
            'portas': forms.NumberInput(attrs={'class': FC, 'min': 2, 'max': 5}),
            'lugares': forms.NumberInput(attrs={'class': FC, 'min': 2, 'max': 9}),
            'km_atual': forms.NumberInput(attrs={'class': FC}),
            'grupo': forms.Select(attrs={'class': FS}),
            'situacao': forms.Select(attrs={'class': FS}),
            'data_aquisicao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'valor_aquisicao': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'valor_fipe': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
        }

    def clean_placa(self):
        from apps.core.validators import validar_placa
        from django.core.exceptions import ValidationError as DjangoValidationError
        placa = self.cleaned_data['placa'].upper().replace(' ', '').replace('-', '')
        # Normaliza para o formato com hifen se padrao antigo
        import re
        if re.match(r'^[A-Z]{3}\d{4}$', placa):
            placa = f'{placa[:3]}-{placa[3:]}'
        try:
            validar_placa(placa)
        except DjangoValidationError as e:
            raise forms.ValidationError(e.message)
        return placa


class GrupoVeiculoForm(forms.ModelForm):
    class Meta:
        model = GrupoVeiculo
        fields = ['nome', 'categoria', 'descricao', 'diaria', 'semanal', 'mensal',
                  'km_franquia_diaria', 'valor_km_excedente', 'caucao', 'ativo']
        widgets = {
            'nome': forms.TextInput(attrs={'class': FC}),
            'categoria': forms.Select(attrs={'class': FS}),
            'descricao': forms.Textarea(attrs={'class': FC, 'rows': 3}),
            'diaria': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'semanal': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'mensal': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'km_franquia_diaria': forms.NumberInput(attrs={'class': FC}),
            'valor_km_excedente': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'caucao': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class FotoVeiculoForm(forms.ModelForm):
    class Meta:
        model = FotoVeiculo
        fields = ['imagem', 'posicao', 'legenda', 'principal']
        widgets = {
            'imagem': forms.FileInput(attrs={'class': FC}),
            'posicao': forms.Select(attrs={'class': FS}),
            'legenda': forms.TextInput(attrs={'class': FC}),
            'principal': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class DocumentoVeiculoForm(forms.ModelForm):
    class Meta:
        model = DocumentoVeiculo
        fields = ['tipo', 'numero', 'emissor', 'data_emissao', 'data_validade', 'arquivo', 'dias_alerta']
        widgets = {
            'tipo': forms.Select(attrs={'class': FS}),
            'numero': forms.TextInput(attrs={'class': FC}),
            'emissor': forms.TextInput(attrs={'class': FC}),
            'data_emissao': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'data_validade': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'arquivo': forms.FileInput(attrs={'class': FC}),
            'dias_alerta': forms.NumberInput(attrs={'class': FC}),
        }


class CategoriaVeiculoForm(forms.ModelForm):
    class Meta:
        model = CategoriaVeiculo
        fields = ['nome', 'icone', 'cor', 'ordem', 'ativo']
        widgets = {
            'nome': forms.TextInput(attrs={'class': FC, 'placeholder': 'Ex: Econômico, SUV, Utilitário'}),
            'icone': forms.TextInput(attrs={'class': FC, 'placeholder': 'Ex: bi-car-front-fill'}),
            'cor': forms.TextInput(attrs={'class': FC, 'type': 'color'}),
            'ordem': forms.NumberInput(attrs={'class': FC, 'min': 0}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
