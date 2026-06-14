import calendar
from datetime import date
from decimal import Decimal, ROUND_DOWN
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class ConfiguracaoLocadora(models.Model):
    """Configuracoes financeiras da locadora (uma por tenant)."""

    percentual_multa_atraso = models.DecimalField(
        'Multa por atraso (%)', max_digits=5, decimal_places=2, default=Decimal('2.00'),
        help_text='Percentual aplicado sobre o valor da parcela em atraso'
    )
    percentual_juros_diario = models.DecimalField(
        'Juros diario (%)', max_digits=6, decimal_places=4, default=Decimal('0.0333'),
        help_text='1% ao mes = 0.0333% ao dia'
    )
    dias_carencia = models.PositiveSmallIntegerField(
        'Dias de carencia', default=0,
        help_text='Dias apos o vencimento antes de aplicar multa e juros'
    )
    custo_reposicao_combustivel = models.DecimalField(
        'Custo reposicao combustivel (R$/1/4 tanque)',
        max_digits=8, decimal_places=2, default=Decimal('0.00'),
        help_text='Valor cobrado por quarto de tanque faltante na devolucao. 0 = nao cobrar.'
    )

    class Meta:
        verbose_name = 'Configuracao da Locadora'
        verbose_name_plural = 'Configuracoes da Locadora'

    def __str__(self):
        return 'Configuracao Financeira'

    @classmethod
    def obter(cls):
        """Retorna a configuracao existente ou cria uma com valores padrao."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class DespesaOperacional(models.Model):
    CATEGORIA = [
        ('manutencao', 'Manutencao'),
        ('seguro', 'Seguro'),
        ('ipva', 'IPVA'),
        ('licenciamento', 'Licenciamento / CRLV'),
        ('combustivel', 'Combustivel'),
        ('lavagem', 'Lavagem / Higienizacao'),
        ('aluguel', 'Aluguel do Estabelecimento'),
        ('salario', 'Folha de Pagamento'),
        ('marketing', 'Marketing / Publicidade'),
        ('outros', 'Outros'),
    ]
    FORMA = [
        ('cartao_credito', 'Cartao de Credito'),
        ('cartao_debito', 'Cartao de Debito'),
        ('boleto', 'Boleto Bancario'),
        ('cheque', 'Cheque'),
        ('pix', 'PIX'),
        ('transferencia', 'Transferencia Bancaria'),
        ('dinheiro', 'Dinheiro'),
    ]

    categoria = models.CharField('Categoria', max_length=20, choices=CATEGORIA)
    descricao = models.CharField('Descricao', max_length=300)
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2)
    data_competencia = models.DateField('Data de competencia', default=timezone.now)
    data_pagamento = models.DateField('Data de pagamento', null=True, blank=True)
    parcelado = models.BooleanField('Parcelado', default=False)
    numero_parcelas = models.PositiveSmallIntegerField('Numero de parcelas', null=True, blank=True)
    forma_pagamento = models.CharField(
        'Forma de pagamento', max_length=20, choices=FORMA, blank=True
    )
    veiculo = models.ForeignKey(
        'fleet.Veiculo', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Veiculo', related_name='despesas'
    )
    os_origem = models.OneToOneField(
        'manutencao.OrdemManutencao', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='OS de origem',
        related_name='despesa_gerada'
    )
    debito_automatico = models.BooleanField(
        'Débito automático',
        default=False,
        help_text='Marque se as parcelas são debitadas automaticamente (ex: cartão de crédito). '
                  'O sistema as confirmará na data de vencimento sem ação manual.'
    )
    observacoes = models.TextField('Observacoes', blank=True)
    criado_por = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Criado por'
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Despesa Operacional'
        verbose_name_plural = 'Despesas Operacionais'
        ordering = ['-data_competencia']
        indexes = [
            models.Index(fields=['data_competencia']),
            models.Index(fields=['categoria']),
            models.Index(fields=['parcelado']),
        ]

    def __str__(self):
        return f'{self.get_categoria_display()} - R$ {self.valor} ({self.data_competencia.strftime("%d/%m/%Y")})'

    @property
    def pago(self):
        if self.parcelado:
            # Usa prefetch_related se disponível, evitando query extra na listagem
            parcelas = list(self.parcelas.all())
            return len(parcelas) > 0 and all(p.situacao == 'pago' for p in parcelas)
        return self.data_pagamento is not None

    @classmethod
    def sincronizar_auto_pagamento(cls):
        """Confirma as parcelas de débito automático cujo vencimento já passou.

        Chamada de forma lazy nas views relevantes — dispara um único UPDATE no banco.
        Retorna a quantidade de parcelas confirmadas.
        """
        hoje = timezone.now().date()
        return ParcelaDespesa.objects.filter(
            despesa__debito_automatico=True,
            situacao__in=['pendente', 'em_atraso'],
            data_vencimento__lte=hoje,
        ).update(situacao='pago', data_pagamento=hoje)

    @property
    def parcelas_pagas(self):
        return sum(1 for p in self.parcelas.all() if p.situacao == 'pago')

    @property
    def progresso_parcelas(self):
        """Retorna (pagas, total) para exibir progresso."""
        if not self.parcelado:
            return None
        todas = list(self.parcelas.all())
        pagas = sum(1 for p in todas if p.situacao == 'pago')
        return pagas, len(todas)


class MultaTransito(models.Model):
    SITUACAO = [
        ('pendente_identificacao', 'Pendente - Identificar Condutor'),
        ('identificada', 'Condutor Identificado'),
        ('cobrada_cliente', 'Cobrada ao Cliente'),
        ('paga', 'Paga'),
        ('contestada', 'Em Contestacao'),
    ]

    veiculo = models.ForeignKey(
        'fleet.Veiculo', on_delete=models.PROTECT,
        verbose_name='Veiculo', related_name='multas'
    )
    contrato = models.ForeignKey(
        'contracts.Contrato', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Contrato', related_name='multas'
    )

    numero_auto = models.CharField('No do Auto de Infracao', max_length=30, blank=True)
    data_infracao = models.DateField('Data da Infracao')
    data_notificacao = models.DateField('Data de Notificacao', null=True, blank=True)
    prazo_indicacao = models.DateField('Prazo para Indicar Condutor', null=True, blank=True)

    descricao = models.TextField('Descricao da Infracao')
    pontos = models.PositiveSmallIntegerField('Pontos', default=0)
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2)

    condutor_nome = models.CharField('Nome do Condutor', max_length=200, blank=True)
    condutor_cpf = models.CharField('CPF do Condutor', max_length=14, blank=True)

    situacao = models.CharField('Situacao', max_length=30, choices=SITUACAO, default='pendente_identificacao')
    observacoes = models.TextField('Observacoes', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Multa de Transito'
        verbose_name_plural = 'Multas de Transito'
        ordering = ['-data_infracao']

    def __str__(self):
        return f'{self.veiculo.placa} - {self.data_infracao.strftime("%d/%m/%Y")} - R$ {self.valor}'

    def save(self, *args, **kwargs):
        if not self.contrato_id and self.veiculo_id and self.data_infracao:
            from django.db.models import Q
            from apps.contracts.models import Contrato
            # Para contratos encerrados usa data_devolucao_real; para os demais usa data_devolucao_prevista
            contrato = Contrato.objects.filter(
                veiculo=self.veiculo,
                data_saida__date__lte=self.data_infracao,
                situacao__in=['ativo', 'encerrado', 'aguardando_devolucao'],
            ).filter(
                Q(situacao='encerrado', data_devolucao_real__date__gte=self.data_infracao)
                | Q(situacao__in=['ativo', 'aguardando_devolucao'], data_devolucao_prevista__date__gte=self.data_infracao)
            ).first()
            if contrato:
                self.contrato = contrato
        super().save(*args, **kwargs)

    @property
    def prazo_critico(self):
        if self.prazo_indicacao and self.situacao == 'pendente_identificacao':
            delta = self.prazo_indicacao - timezone.now().date()
            return delta.days <= 5
        return False

    @property
    def prazo_vencido(self):
        if self.prazo_indicacao:
            return self.prazo_indicacao < timezone.now().date()
        return False


class ContaReceber(models.Model):
    SITUACAO = [
        ('pendente', 'Pendente'),
        ('pago_parcial', 'Pago Parcialmente'),
        ('pago', 'Pago'),
        ('vencido', 'Vencido'),
        ('cancelado', 'Cancelado'),
    ]
    COR_SITUACAO = {
        'pendente':     ('#f59e0b', '#fffbeb'),
        'pago_parcial': ('#6366f1', '#eef2ff'),
        'pago':         ('#10b981', '#ecfdf5'),
        'vencido':      ('#ef4444', '#fef2f2'),
        'cancelado':    ('#94a3b8', '#f8fafc'),
    }

    contrato = models.OneToOneField(
        'contracts.Contrato', on_delete=models.PROTECT,
        verbose_name='Contrato', related_name='conta_receber'
    )
    cliente = models.ForeignKey(
        'customers.Cliente', on_delete=models.PROTECT,
        verbose_name='Cliente', related_name='contas_receber'
    )
    descricao = models.CharField('Descricao', max_length=300)
    valor_total = models.DecimalField('Valor Total (R$)', max_digits=10, decimal_places=2)
    valor_pago = models.DecimalField('Valor Pago (R$)', max_digits=10, decimal_places=2, default=Decimal('0.00'))
    data_emissao = models.DateField('Data de Emissao')
    data_vencimento = models.DateField('Data de Vencimento')
    situacao = models.CharField('Situacao', max_length=20, choices=SITUACAO, default='pendente')
    observacoes = models.TextField('Observacoes', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conta a Receber'
        verbose_name_plural = 'Contas a Receber'
        ordering = ['data_vencimento']
        indexes = [
            models.Index(fields=['situacao']),
            models.Index(fields=['data_vencimento']),
            models.Index(fields=['cliente']),
        ]

    def __str__(self):
        return f'{self.contrato.numero} - R$ {self.valor_total} ({self.get_situacao_display()})'

    @property
    def valor_saldo(self):
        return self.valor_total - self.valor_pago

    @property
    def vencida(self):
        return (
            self.data_vencimento < timezone.now().date()
            and self.situacao not in ('pago', 'cancelado')
        )

    @property
    def dias_em_atraso(self):
        if self.vencida:
            return (timezone.now().date() - self.data_vencimento).days
        return 0

    @property
    def dias_para_vencer(self):
        if self.situacao in ('pago', 'cancelado'):
            return None
        return (self.data_vencimento - timezone.now().date()).days

    @property
    def cor_situacao(self):
        return self.COR_SITUACAO.get(self.situacao, ('#94a3b8', '#f8fafc'))

    def atualizar_situacao(self, novo_valor_total=None, novo_vencimento=None):
        """Recalcula valor_pago e situacao a partir dos pagamentos de locação do contrato.

        novo_valor_total: atualiza valor_total (ex: prorrogação ou fechamento).
        novo_vencimento: atualiza data_vencimento (ex: prorrogação).
        Caução é excluído do total_pago pois é depósito, não receita de locação.
        """
        if self.situacao == 'cancelado':
            return

        from django.db import transaction
        with transaction.atomic():
            obj = ContaReceber.objects.select_for_update().get(pk=self.pk)
            if obj.situacao == 'cancelado':
                return

            if novo_valor_total is not None:
                obj.valor_total = novo_valor_total

            if novo_vencimento is not None:
                obj.data_vencimento = novo_vencimento

            from apps.contracts.models import PagamentoContrato
            # Exclui caucao: é depósito devolvível, não pagamento de locação
            total = PagamentoContrato.objects.filter(
                contrato=obj.contrato
            ).exclude(tipo='caucao').aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

            obj.valor_pago = total
            saldo = obj.valor_total - total

            if saldo <= Decimal('0.00'):
                obj.situacao = 'pago'
            elif total > Decimal('0.00'):
                obj.situacao = 'pago_parcial'
            elif obj.vencida:
                obj.situacao = 'vencido'
            else:
                obj.situacao = 'pendente'

            campos = ['situacao', 'valor_pago', 'atualizado_em']
            if novo_valor_total is not None:
                campos.append('valor_total')
            if novo_vencimento is not None:
                campos.append('data_vencimento')
            obj.save(update_fields=campos)
            self.situacao = obj.situacao
            self.valor_pago = obj.valor_pago
            if novo_valor_total is not None:
                self.valor_total = obj.valor_total
            if novo_vencimento is not None:
                self.data_vencimento = obj.data_vencimento


# ─── Parcelas de Despesa ─────────────────────────────────────────────────────

class ParcelaDespesa(models.Model):
    SITUACAO = [
        ('pendente', 'Pendente'),
        ('pago', 'Pago'),
        ('em_atraso', 'Em Atraso'),
    ]
    FORMA = [
        ('dinheiro', 'Dinheiro'),
        ('pix', 'PIX'),
        ('cartao_credito', 'Cartão de Crédito'),
        ('cartao_debito', 'Cartão de Débito'),
        ('boleto', 'Boleto Bancário'),
        ('transferencia', 'Transferência Bancária'),
        ('debito_automatico', 'Débito Automático'),
    ]

    despesa = models.ForeignKey(
        DespesaOperacional, on_delete=models.CASCADE,
        verbose_name='Despesa', related_name='parcelas'
    )
    numero = models.PositiveSmallIntegerField('No da Parcela')
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2)
    data_vencimento = models.DateField('Vencimento')
    situacao = models.CharField('Situacao', max_length=10, choices=SITUACAO, default='pendente')
    data_pagamento = models.DateField('Pago em', null=True, blank=True)
    forma_pagamento = models.CharField('Forma de pagamento', max_length=20, choices=FORMA, blank=True)
    observacoes = models.TextField('Observacoes', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Parcela de Despesa'
        verbose_name_plural = 'Parcelas de Despesa'
        ordering = ['despesa', 'numero']
        indexes = [
            models.Index(fields=['situacao']),
            models.Index(fields=['data_vencimento']),
            models.Index(fields=['despesa', 'situacao']),
        ]

    def __str__(self):
        return f'Parcela {self.numero}/{self.despesa.numero_parcelas} — {self.despesa.descricao}'

    @property
    def em_atraso(self):
        return self.situacao == 'em_atraso' or (
            self.situacao == 'pendente' and self.data_vencimento < timezone.now().date()
        )

    @property
    def dias_atraso(self):
        if self.em_atraso:
            return (timezone.now().date() - self.data_vencimento).days
        return 0


def _adicionar_meses(data_base, meses):
    """Soma N meses a data_base respeitando fim de mês (sem dependência extra)."""
    total_mes = data_base.month + meses
    ano = data_base.year + (total_mes - 1) // 12
    mes = (total_mes - 1) % 12 + 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(data_base.day, ultimo_dia))


def gerar_parcelas_despesa(despesa):
    """Cria as ParcelaDespesa mensais para uma despesa parcelada.

    A primeira parcela vence no mês seguinte ao da data_competencia,
    distribuindo o total em despesa.numero_parcelas prestações iguais;
    o centavo residual vai para a última parcela.
    """
    n = despesa.numero_parcelas
    valor_parcela = (despesa.valor / n).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    resto = despesa.valor - (valor_parcela * n)
    auto = despesa.debito_automatico
    hoje = date.today()

    parcelas = []
    for i in range(1, n + 1):
        valor = valor_parcela + (resto if i == n else Decimal('0.00'))
        vencimento = _adicionar_meses(despesa.data_competencia, i)
        # Parcelas já vencidas de cartão de crédito (débito automático) chegam como pagas.
        if auto and vencimento <= hoje:
            situacao = 'pago'
            data_pagamento = vencimento
            forma_pag = despesa.forma_pagamento or ''
        else:
            situacao = 'pendente'
            data_pagamento = None
            forma_pag = ''
        parcelas.append(ParcelaDespesa(
            despesa=despesa,
            numero=i,
            valor=valor,
            data_vencimento=vencimento,
            situacao=situacao,
            data_pagamento=data_pagamento,
            forma_pagamento=forma_pag,
        ))
    ParcelaDespesa.objects.bulk_create(parcelas)
