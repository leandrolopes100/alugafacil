from datetime import timedelta
from decimal import Decimal

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
    # Preenchidos apenas em registro retroativo/migração (veículo já estava
    # com o cliente antes do cadastro no sistema) — opcionais no fluxo normal.
    data_saida = forms.DateTimeField(
        label='Data/hora de saída real', required=False,
        widget=forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'}),
    )
    km_saida = forms.IntegerField(
        label='KM de saída (na época)', required=False, min_value=0,
        widget=forms.NumberInput(attrs={'class': FC}),
    )
    combustivel_saida = forms.ChoiceField(
        label='Combustível na saída', required=False,
        choices=[('', '— selecione —')] + Contrato.COMBUSTIVEL,
        widget=forms.Select(attrs={'class': FS}),
    )
    km_atual_veiculo = forms.IntegerField(
        label='KM atual do veículo (hoje)', required=False, min_value=0,
        widget=forms.NumberInput(attrs={'class': FC}),
        help_text='Opcional — se souber o hodômetro atual, informe para manter o KM do veículo em dia.',
    )
    valor_ja_recebido = forms.DecimalField(
        label='Valor já recebido até hoje', required=False, min_value=Decimal('0'),
        max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
        help_text='Valor referente ao período retroativo já pago pelo cliente fora do sistema.',
    )
    forma_pagamento_regularizacao = forms.ChoiceField(
        label='Forma de pagamento do valor já recebido', required=False,
        choices=[('', '— selecione —')] + PagamentoContrato.FORMAS_RECEBIMENTO,
        widget=forms.Select(attrs={'class': FS}),
    )
    dias_correntes_pagos = forms.BooleanField(
        label='Os dias de hoje até o próximo vencimento já estão pagos no valor acima',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    justificativa_retroativa = forms.CharField(
        label='Justificativa da migração', required=False,
        widget=forms.Textarea(attrs={'class': FC, 'rows': 2}),
    )

    class Meta:
        model = Contrato
        fields = ['cliente', 'veiculo', 'data_devolucao_prevista',
                  'diaria', 'forma_cobranca', 'valor_semanal', 'valor_mensal',
                  'km_franquia_diaria', 'valor_km_excedente',
                  'caucao_valor', 'caucao_situacao']
        widgets = {
            'cliente': forms.Select(attrs={'class': FS}),
            'veiculo': forms.Select(attrs={'class': FS}),  # select renderizado manualmente no template
            'data_devolucao_prevista': forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'}),
            'diaria': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'forma_cobranca': forms.Select(attrs={'class': FS}),
            'valor_semanal': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'valor_mensal': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'km_franquia_diaria': forms.NumberInput(attrs={'class': FC}),
            'valor_km_excedente': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'caucao_valor': forms.NumberInput(attrs={'class': FC, 'step': '0.01'}),
            'caucao_situacao': forms.Select(attrs={'class': FS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['forma_cobranca'].required = True
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

    def clean(self):
        from apps.fleet.tarifas import conferir_tarifas
        cleaned = super().clean()
        for campo in ('valor_semanal', 'valor_mensal'):
            valor = cleaned.get(campo)
            if valor is not None and valor <= 0:
                cleaned[campo] = None
        diaria = cleaned.get('diaria')
        if diaria and diaria > 0:
            nomes = {'semanal': 'valor_semanal', 'mensal': 'valor_mensal'}
            for erro in conferir_tarifas(diaria, cleaned.get('valor_semanal'), cleaned.get('valor_mensal'))['erros']:
                self.add_error(nomes[erro['campo']], erro['texto'])

        data_saida = cleaned.get('data_saida')
        if not data_saida:
            return cleaned

        agora = timezone.now()
        if data_saida > agora:
            self.add_error('data_saida', 'A data de saída retroativa não pode ser no futuro.')

        data_devolucao_prevista = cleaned.get('data_devolucao_prevista')
        if data_devolucao_prevista and data_saida >= data_devolucao_prevista:
            self.add_error('data_saida', 'A data de saída deve ser anterior à devolução prevista.')

        if cleaned.get('km_saida') is None:
            self.add_error('km_saida', 'Informe o KM de saída para o registro retroativo.')

        if not cleaned.get('combustivel_saida'):
            self.add_error('combustivel_saida', 'Informe o combustível de saída para o registro retroativo.')

        if not (cleaned.get('justificativa_retroativa') or '').strip():
            self.add_error('justificativa_retroativa', 'Informe a justificativa para o registro retroativo.')

        if (cleaned.get('valor_ja_recebido') or 0) > 0 and not cleaned.get('forma_pagamento_regularizacao'):
            self.add_error('forma_pagamento_regularizacao', 'Informe como o cliente pagou o valor já recebido.')

        km_saida = cleaned.get('km_saida')
        km_atual_veiculo = cleaned.get('km_atual_veiculo')
        if km_atual_veiculo is not None and km_saida is not None and km_atual_veiculo < km_saida:
            self.add_error('km_atual_veiculo', 'O KM atual não pode ser menor que o KM de saída informado.')

        return cleaned


class CheckoutForm(forms.ModelForm):
    # Campo extra (não-model) para registrar como o caução foi pago no checkout
    caucao_forma_pagamento = forms.ChoiceField(
        choices=[('', '— selecione —')] + PagamentoContrato.FORMAS_RECEBIMENTO,
        required=False,
        widget=forms.Select(attrs={'class': FS}),
        label='Forma de Recebimento',
    )

    class Meta:
        model = Contrato
        fields = ['data_devolucao_prevista', 'km_saida', 'combustivel_saida', 'obs_saida', 'caucao_situacao']
        widgets = {
            'data_devolucao_prevista': forms.DateTimeInput(
                format='%Y-%m-%dT%H:%M', attrs={'class': FC, 'type': 'datetime-local'},
            ),
            'km_saida': forms.NumberInput(attrs={'class': FC}),
            'combustivel_saida': forms.Select(attrs={'class': FS}),
            'obs_saida': forms.Textarea(attrs={'class': FC, 'rows': 3}),
            'caucao_situacao': forms.Select(attrs={'class': FS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['combustivel_saida'].required = True
        self.fields['data_devolucao_prevista'].required = False
        # Dropdown visível quando caucao_situacao='pendente'
        choices_checkout = [
            ('pendente', 'Pendente — cobrar depois'),
            ('pago', 'Pago — coletado agora'),
        ]
        # Se o caução já tem um estado além de pendente/pago (ex: 'retido'),
        # inclui como choice válida — o template envia via hidden input e o
        # Django valida contra essa lista. Sem isso, form.is_valid() retorna False.
        if self.instance and self.instance.pk:
            current = self.instance.caucao_situacao
            if current not in ('pendente', 'pago'):
                choices_checkout.append((current, self.instance.get_caucao_situacao_display()))
        self.fields['caucao_situacao'].choices = choices_checkout

    def clean_data_devolucao_prevista(self):
        data = self.cleaned_data.get('data_devolucao_prevista') or self.instance.data_devolucao_prevista
        if data and data <= timezone.now():
            raise forms.ValidationError(
                'A devolução prevista já passou. Informe a nova data e hora de devolução antes de liberar o veículo.'
            )
        return data

    def clean(self):
        cleaned = super().clean()
        situacao = cleaned.get('caucao_situacao')
        forma = cleaned.get('caucao_forma_pagamento')
        caucao_valor = getattr(self.instance, 'caucao_valor', 0)
        if caucao_valor and caucao_valor > 0 and situacao == 'pago' and not forma:
            self.add_error('caucao_forma_pagamento', 'Informe a forma de recebimento do caução.')
        return cleaned

    def clean_km_saida(self):
        km = self.cleaned_data.get('km_saida')
        if km is not None and km < 0:
            raise forms.ValidationError('A quilometragem não pode ser negativa.')
        return km


class CheckinForm(forms.ModelForm):
    FOLGA_RELOGIO = timedelta(minutes=5)
    motivo_sempre_obrigatorio = False

    class Meta:
        model = Contrato
        fields = ['data_devolucao_real', 'km_devolucao', 'combustivel_devolucao', 'obs_devolucao',
                  'motivo_encerramento', 'motivo_encerramento_detalhe']
        widgets = {
            'data_devolucao_real': forms.DateTimeInput(attrs={'class': FC, 'type': 'datetime-local'}),
            'km_devolucao': forms.NumberInput(attrs={'class': FC}),
            'combustivel_devolucao': forms.Select(attrs={'class': FS}),
            'obs_devolucao': forms.Textarea(attrs={'class': FC, 'rows': 3}),
            'motivo_encerramento': forms.Select(attrs={'class': FS}),
            'motivo_encerramento_detalhe': forms.Textarea(attrs={'class': FC, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['data_devolucao_real'].required = True
        self.fields['combustivel_devolucao'].required = True
        self.fields['motivo_encerramento'].required = self.motivo_sempre_obrigatorio
        self.fields['motivo_encerramento'].choices = (
            [('', '— selecione —')] + Contrato.MOTIVO_ENCERRAMENTO_ANTECIPADO
        )
        if not self.initial.get('data_devolucao_real') and not (
            self.instance and self.instance.pk and self.instance.data_devolucao_real
        ):
            self.initial['data_devolucao_real'] = timezone.localtime(timezone.now()).strftime('%Y-%m-%dT%H:%M')

    def clean_data_devolucao_real(self):
        dt = self.cleaned_data.get('data_devolucao_real')
        if dt and timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        if not dt:
            return dt
        saida = self.instance.data_saida
        if saida and dt < saida:
            raise forms.ValidationError(
                f'A devolução não pode ser antes da saída do veículo '
                f'({timezone.localtime(saida):%d/%m/%Y %H:%M}).'
            )
        if dt > timezone.now() + self.FOLGA_RELOGIO:
            raise forms.ValidationError(
                'A devolução não pode ser registrada no futuro. Informe a data e hora em que o veículo chegou.'
            )
        return dt

    def clean(self):
        cleaned = super().clean()
        motivo = cleaned.get('motivo_encerramento')
        detalhe = (cleaned.get('motivo_encerramento_detalhe') or '').strip()
        data = cleaned.get('data_devolucao_real')
        antecipada = self.motivo_sempre_obrigatorio or bool(
            data and self.instance.data_saida and self.instance.parcelas_futuras_abertas(data).exists()
        )
        if not antecipada:
            cleaned['motivo_encerramento'] = ''
            cleaned['motivo_encerramento_detalhe'] = ''
            return cleaned
        if not motivo:
            self.add_error(
                'motivo_encerramento',
                'O veículo está voltando antes do prazo e semanas futuras serão canceladas: informe o motivo.'
            )
        elif motivo == 'outro' and not detalhe:
            self.add_error('motivo_encerramento_detalhe', 'Detalhe o motivo quando selecionar "Outro".')
        return cleaned

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


class EncerramentoAntecipadoForm(CheckinForm):
    motivo_sempre_obrigatorio = True


class AdicionalContratoForm(forms.ModelForm):
    class Meta:
        model = AdicionalContrato
        fields = ['tipo', 'descricao', 'diaria', 'quantidade']
        widgets = {
            'tipo': forms.Select(attrs={'class': FS}),
            'descricao': forms.TextInput(attrs={'class': FC}),
            'diaria': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0.01'}),
            'quantidade': forms.NumberInput(attrs={'class': FC, 'min': 1}),
        }


class AvariaContratoForm(forms.ModelForm):
    class Meta:
        model = AvariaContrato
        fields = ['descricao', 'localizacao', 'valor_cobrado', 'foto', 'situacao']
        widgets = {
            'descricao': forms.TextInput(attrs={'class': FC}),
            'localizacao': forms.TextInput(attrs={'class': FC}),
            'valor_cobrado': forms.NumberInput(attrs={'class': FC, 'step': '0.01', 'min': '0'}),
            'foto': forms.FileInput(attrs={'class': FC}),
            'situacao': forms.Select(attrs={'class': FS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 'paga' só pode ser definido via ContratoAvariaMarcarPagaView (cria PagamentoContrato junto)
        self.fields['situacao'].choices = [
            c for c in AvariaContrato.SITUACAO if c[0] != 'paga'
        ]


class AjusteAvariaForm(forms.ModelForm):
    class Meta:
        model = AvariaContrato
        fields = ['valor_cobrado', 'situacao']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['situacao'].choices = [
            c for c in AvariaContrato.SITUACAO if c[0] in AvariaContrato.SITUACOES_AJUSTAVEIS
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['forma_pagamento'].choices = [('', '---------')] + PagamentoContrato.FORMAS_RECEBIMENTO
        self.fields['tipo'].choices = [
            c for c in PagamentoContrato.TIPO if c[0] in Contrato.TIPOS_RECEBIMENTO
        ]

    def clean_data_pagamento(self):
        data = self.cleaned_data.get('data_pagamento')
        if data and data > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError('O pagamento não pode ter data futura.')
        return data

    def clean_valor(self):
        valor = self.cleaned_data.get('valor')
        if valor is not None and valor <= 0:
            raise forms.ValidationError('O valor do pagamento deve ser maior que zero.')
        return valor
