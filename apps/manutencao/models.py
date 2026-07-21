from django.db import models
from django.utils import timezone


class OrdemManutencao(models.Model):
    TIPO = [
        ('preventiva', 'Preventiva'),
        ('corretiva', 'Corretiva'),
        ('sinistro', 'Sinistro/Acidente'),
    ]
    SITUACAO = [
        ('agendada', 'Agendada'),
        ('em_andamento', 'Em Andamento'),
        ('concluida', 'Concluída'),
        ('cancelada', 'Cancelada'),
    ]

    veiculo = models.ForeignKey(
        'fleet.Veiculo', on_delete=models.PROTECT,
        verbose_name='Veículo', related_name='manutencoes'
    )
    tipo = models.CharField('Tipo', max_length=20, choices=TIPO, default='preventiva')
    situacao = models.CharField('Situação', max_length=20, choices=SITUACAO, default='agendada')
    descricao = models.CharField('Descrição do serviço', max_length=300)
    observacoes = models.TextField('Observações', blank=True)

    km_na_manutencao = models.PositiveIntegerField('KM na manutenção', null=True, blank=True)
    data_agendada = models.DateField('Data agendada', null=True, blank=True)
    data_entrada = models.DateField('Entrada na oficina', null=True, blank=True)
    data_saida = models.DateField('Saída da oficina', null=True, blank=True)

    fornecedor = models.CharField('Oficina / Fornecedor', max_length=200, blank=True)
    custo_total = models.DecimalField('Custo total (R$)', max_digits=10, decimal_places=2, null=True, blank=True)

    contrato = models.ForeignKey(
        'contracts.Contrato', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Contrato relacionado', related_name='manutencoes'
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Ordem de Manutenção'
        verbose_name_plural = 'Ordens de Manutenção'
        ordering = ['-criado_em']

    def __str__(self):
        return f'OS {self.pk} — {self.veiculo.placa} — {self.descricao[:50]}'

    def save(self, *args, **kwargs):
        old_situacao = None
        if self.pk:
            try:
                old_situacao = OrdemManutencao.objects.values_list(
                    'situacao', flat=True
                ).get(pk=self.pk)
            except OrdemManutencao.DoesNotExist:
                pass
        super().save(*args, **kwargs)
        if self.situacao == 'concluida' and self.custo_total:
            self._sincronizar_despesa()
        elif self.situacao == 'cancelada' or (
            old_situacao == 'concluida' and self.situacao != 'concluida'
        ):
            self._remover_despesa_gerada()

    def _sincronizar_despesa(self):
        from apps.financeiro.models import DespesaOperacional
        descricao = f'Manutencao OS #{self.pk} — {self.descricao[:100]}'
        data_comp = self.data_saida or timezone.now().date()
        obs = f'Gerada automaticamente pela OS #{self.pk}.'
        if self.fornecedor:
            obs += f' Fornecedor: {self.fornecedor}.'
        try:
            despesa = DespesaOperacional.objects.get(os_origem=self)
            despesa.valor = self.custo_total
            despesa.descricao = descricao
            despesa.data_competencia = data_comp
            despesa.observacoes = obs
            despesa.save(update_fields=['valor', 'descricao', 'data_competencia', 'observacoes'])
        except DespesaOperacional.DoesNotExist:
            DespesaOperacional.objects.create(
                os_origem=self,
                categoria='manutencao',
                descricao=descricao,
                valor=self.custo_total,
                data_competencia=data_comp,
                veiculo=self.veiculo,
                observacoes=obs,
            )

    def _remover_despesa_gerada(self):
        from apps.financeiro.models import DespesaOperacional
        DespesaOperacional.objects.filter(os_origem=self).delete()


class AlertaManutencao(models.Model):
    TIPO = [
        ('km', 'Por Quilometragem'),
        ('data', 'Por Data'),
    ]

    veiculo = models.ForeignKey(
        'fleet.Veiculo', on_delete=models.CASCADE,
        verbose_name='Veículo', related_name='alertas_manutencao'
    )
    descricao = models.CharField('Descrição', max_length=200,
                                  help_text='Ex: Troca de óleo, Revisão dos freios')
    tipo_alerta = models.CharField('Tipo de alerta', max_length=5, choices=TIPO)

    # Por KM
    km_proximo_servico = models.PositiveIntegerField(
        'KM do próximo serviço', null=True, blank=True)
    km_intervalo = models.PositiveIntegerField(
        'Repetir a cada X km', null=True, blank=True,
        help_text='0 = não repetir')

    # Por Data
    data_proximo_servico = models.DateField(
        'Data do próximo serviço', null=True, blank=True)

    ativo = models.BooleanField('Ativo', default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Alerta de Manutenção'
        verbose_name_plural = 'Alertas de Manutenção'
        ordering = ['veiculo', 'tipo_alerta']

    def __str__(self):
        return f'{self.veiculo.placa} — {self.descricao}'

    @property
    def vencido(self):
        if self.tipo_alerta == 'km' and self.km_proximo_servico:
            return self.veiculo.km_atual >= self.km_proximo_servico
        if self.tipo_alerta == 'data' and self.data_proximo_servico:
            return self.data_proximo_servico <= timezone.now().date()
        return False

    @property
    def proximo(self):
        """Alerta que está perto de vencer mas ainda não venceu."""
        if self.vencido:
            return False
        if self.tipo_alerta == 'km' and self.km_proximo_servico:
            return self.veiculo.km_atual >= self.km_proximo_servico - 500
        if self.tipo_alerta == 'data' and self.data_proximo_servico:
            delta = self.data_proximo_servico - timezone.now().date()
            return delta.days <= 7
        return False
