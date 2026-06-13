from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Investidor(models.Model):
    TIPO = [('pf', 'Pessoa Física'), ('pj', 'Pessoa Jurídica')]
    SITUACAO = [('ativo', 'Ativo'), ('inativo', 'Inativo')]

    tipo = models.CharField('Tipo', max_length=2, choices=TIPO, default='pf')
    nome = models.CharField('Nome completo', max_length=200)
    cpf = models.CharField('CPF', max_length=14, blank=True)
    razao_social = models.CharField('Razão Social', max_length=200, blank=True)
    cnpj = models.CharField('CNPJ', max_length=18, blank=True)
    email = models.EmailField('E-mail', blank=True)
    telefone = models.CharField('Telefone', max_length=20, blank=True)
    celular = models.CharField('Celular', max_length=20, blank=True)
    dados_bancarios = models.TextField(
        'Dados bancários', blank=True,
        help_text='Banco, agência, conta corrente, chave PIX…',
    )
    observacoes = models.TextField('Observações', blank=True)
    situacao = models.CharField('Situação', max_length=10, choices=SITUACAO, default='ativo')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Investidor'
        verbose_name_plural = 'Investidores'
        ordering = ['nome']
        indexes = [
            models.Index(fields=['situacao']),
            models.Index(fields=['nome']),
        ]

    def __str__(self):
        return self.nome_exibicao

    @property
    def nome_exibicao(self):
        if self.tipo == 'pj' and self.razao_social:
            return self.razao_social
        return self.nome

    @property
    def documento(self):
        return self.cnpj if self.tipo == 'pj' else self.cpf


class VeiculoInvestidor(models.Model):
    investidor = models.ForeignKey(
        Investidor, on_delete=models.PROTECT,
        verbose_name='Investidor', related_name='veiculos',
    )
    veiculo = models.ForeignKey(
        'fleet.Veiculo', on_delete=models.PROTECT,
        verbose_name='Veículo', related_name='vinculo_investidor',
    )
    taxa_gestao_semanal = models.DecimalField(
        'Taxa de gestão semanal (R$)', max_digits=10, decimal_places=2,
    )
    dia_vencimento = models.PositiveSmallIntegerField(
        'Dia de vencimento',
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text='Dia do mês em que as cobranças vencem (1 a 28)',
    )
    data_inicio = models.DateField('Data de início')
    data_fim = models.DateField('Data de encerramento', null=True, blank=True)
    ativo = models.BooleanField('Ativo', default=True)
    observacoes = models.TextField('Observações', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Vínculo Veículo–Investidor'
        verbose_name_plural = 'Vínculos Veículo–Investidor'
        ordering = ['-criado_em']
        constraints = [
            models.UniqueConstraint(
                fields=['veiculo'],
                condition=models.Q(ativo=True),
                name='unico_vinculo_ativo_por_veiculo',
            )
        ]

    def __str__(self):
        return f'{self.veiculo} → {self.investidor}'


class CobrancaGestao(models.Model):
    FORMA_PAGAMENTO = [
        ('dinheiro', 'Dinheiro'),
        ('pix', 'PIX'),
        ('transferencia', 'Transferência Bancária'),
        ('boleto', 'Boleto'),
    ]
    SITUACAO = [
        ('pendente', 'Pendente'),
        ('pago', 'Pago'),
        ('cancelado', 'Cancelado'),
    ]
    COR_SITUACAO = {
        'pendente': 'warning',
        'pago': 'success',
        'cancelado': 'neutral',
    }

    veiculo_investidor = models.ForeignKey(
        VeiculoInvestidor, on_delete=models.PROTECT,
        verbose_name='Vínculo', related_name='cobrancas',
    )
    semana_inicio = models.DateField('Semana início')
    semana_fim = models.DateField('Semana fim')
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2)
    data_vencimento = models.DateField('Vencimento')
    situacao = models.CharField('Situação', max_length=10, choices=SITUACAO, default='pendente')
    data_pagamento = models.DateField('Data de pagamento', null=True, blank=True)
    forma_pagamento = models.CharField(
        'Forma de pagamento', max_length=15, choices=FORMA_PAGAMENTO, blank=True,
    )
    observacoes = models.TextField('Observações', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Cobrança de Gestão'
        verbose_name_plural = 'Cobranças de Gestão'
        ordering = ['-semana_inicio', 'veiculo_investidor__investidor__nome']
        indexes = [
            models.Index(fields=['situacao']),
            models.Index(fields=['data_vencimento']),
            models.Index(fields=['veiculo_investidor', 'semana_inicio']),
        ]

    def __str__(self):
        placa = self.veiculo_investidor.veiculo.placa
        return f'{placa} — {self.semana_inicio:%d/%m/%Y} a {self.semana_fim:%d/%m/%Y} — R$ {self.valor}'

    @property
    def cor_situacao(self):
        return self.COR_SITUACAO.get(self.situacao, 'neutral')

    @property
    def em_atraso(self):
        return self.situacao == 'pendente' and self.data_vencimento < timezone.now().date()
