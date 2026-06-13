from django import forms
from .models import AlertaManutencao, OrdemManutencao

FC = 'form-control'
FS = 'form-select'


class OrdemManutencaoForm(forms.ModelForm):
    class Meta:
        model = OrdemManutencao
        fields = ['veiculo', 'tipo', 'situacao', 'descricao', 'km_na_manutencao',
                  'data_agendada', 'data_entrada', 'data_saida',
                  'fornecedor', 'custo_total', 'observacoes']
        widgets = {
            'veiculo': forms.Select(attrs={'class': FS}),
            'tipo': forms.Select(attrs={'class': FS}),
            'situacao': forms.Select(attrs={'class': FS}),
            'descricao': forms.TextInput(attrs={'class': FC}),
            'km_na_manutencao': forms.NumberInput(attrs={'class': FC}),
            'data_agendada': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'data_entrada': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'data_saida': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
            'fornecedor': forms.TextInput(attrs={'class': FC}),
            'custo_total': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'observacoes': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('situacao') == 'concluida' and not cleaned.get('km_na_manutencao'):
            self.add_error('km_na_manutencao', 'KM na manutenção é obrigatório ao concluir a OS.')
        return cleaned


class AlertaManutencaoForm(forms.ModelForm):
    class Meta:
        model = AlertaManutencao
        fields = ['veiculo', 'descricao', 'tipo_alerta',
                  'km_proximo_servico', 'km_intervalo', 'data_proximo_servico']
        widgets = {
            'veiculo': forms.Select(attrs={'class': FS}),
            'descricao': forms.TextInput(attrs={'class': FC, 'placeholder': 'Ex: Troca de óleo'}),
            'tipo_alerta': forms.Select(attrs={'class': FS, 'x-model': 'tipoAlerta'}),
            'km_proximo_servico': forms.NumberInput(attrs={'class': FC}),
            'km_intervalo': forms.NumberInput(attrs={'class': FC}),
            'data_proximo_servico': forms.DateInput(attrs={'class': FC, 'type': 'date'}),
        }
