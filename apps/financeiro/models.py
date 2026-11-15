import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
from django.db import models
from django.db.models import Count, F, Max, Min, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.validators import validar_positivo


class ConfiguracaoLocadora(models.Model):
    """Configuracoes financeiras da locadora (registro único do sistema)."""

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
    tolerancia_devolucao_minutos = models.PositiveSmallIntegerField(
        'Tolerância na devolução (minutos)', default=60,
        help_text='Atraso na devolução, além dos dias contratados, que não gera diária extra. 0 = sem tolerância.'
    )

    TIPO_CHAVE_PIX = [
        ('cpf', 'CPF'),
        ('cnpj', 'CNPJ'),
        ('email', 'E-mail'),
        ('telefone', 'Telefone'),
        ('aleatoria', 'Chave aleatória'),
    ]

    pix_tipo_chave = models.CharField(
        'Tipo da chave Pix', max_length=10, choices=TIPO_CHAVE_PIX, blank=True
    )
    pix_chave = models.CharField('Chave Pix', max_length=77, blank=True)
    pix_nome_recebedor = models.CharField(
        'Nome do recebedor', max_length=25, blank=True,
        help_text='Como aparece para o motorista no app do banco (até 25 caracteres).'
    )
    pix_cidade_recebedor = models.CharField(
        'Cidade do recebedor', max_length=15, blank=True,
        help_text='Cidade da conta que recebe o Pix (até 15 caracteres).'
    )
    whatsapp_locadora = models.CharField(
        'WhatsApp da locadora', max_length=20, blank=True,
        help_text='Número que o motorista usa para enviar o comprovante.'
    )
    usar_modulo_investidores = models.BooleanField(
        'Usar módulo de investidores', default=True,
        help_text='Desligado, o menu de investidores some e os repasses saem do Fluxo de Caixa, '
                  'da DRE, do Dashboard e de Contas a Pagar. Os dados continuam guardados.'
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

    @classmethod
    def modulo_investidores_ativo(cls):
        return cls.obter().usar_modulo_investidores

    @property
    def pix_configurado(self):
        return bool(self.pix_chave and self.pix_nome_recebedor and self.pix_cidade_recebedor)

    def gerar_pix(self, valor, txid=None):
        from .pix import gerar_payload
        if not self.pix_configurado:
            return None
        return gerar_payload(
            self.pix_chave, self.pix_nome_recebedor, self.pix_cidade_recebedor, valor, txid
        )


class DespesaOperacional(models.Model):
    CATEGORIA = [
        ('manutencao', 'Manutenção'),
        ('seguro', 'Seguro'),
        ('ipva', 'IPVA'),
        ('licenciamento', 'Licenciamento / CRLV'),
        ('combustivel', 'Combustível'),
        ('lavagem', 'Lavagem / Higienização'),
        ('aluguel', 'Aluguel do Estabelecimento'),
        ('salario', 'Folha de Pagamento'),
        ('marketing', 'Marketing / Publicidade'),
        ('caucao', 'Devolução de Caução'),
        ('outros', 'Outros'),
    ]
    FORMA = [
        ('cartao_credito', 'Cartão de Crédito'),
        ('cartao_debito', 'Cartão de Débito'),
        ('boleto', 'Boleto Bancário'),
        ('cheque', 'Cheque'),
        ('pix', 'PIX'),
        ('transferencia', 'Transferência Bancária'),
        ('dinheiro', 'Dinheiro'),
    ]

    categoria = models.CharField('Categoria', max_length=20, choices=CATEGORIA)
    descricao = models.CharField('Descricao', max_length=300)
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2, validators=[validar_positivo])
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
        hoje = timezone.localdate()
        forma_da_despesa = cls.objects.filter(pk=OuterRef('despesa_id')).values('forma_pagamento')[:1]
        return ParcelaDespesa.objects.filter(
            despesa__debito_automatico=True,
            situacao__in=['pendente', 'em_atraso'],
            data_vencimento__lte=hoje,
        ).update(
            situacao='pago',
            data_pagamento=F('data_vencimento'),
            forma_pagamento=Subquery(forma_da_despesa),
        )

    @staticmethod
    def parcelas_auto_pagas_fora_do_vencimento():
        return ParcelaDespesa.objects.filter(
            despesa__debito_automatico=True,
            situacao='pago',
            data_pagamento__gt=F('data_vencimento'),
        ).select_related('despesa').order_by('despesa_id', 'numero')

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
        ('contestada', 'Em Contestação'),
    ]
    TRANSICOES = {
        'pendente_identificacao': ['identificada', 'contestada'],
        'identificada': ['cobrada_cliente', 'paga', 'contestada'],
        'cobrada_cliente': ['paga', 'contestada'],
        'contestada': ['identificada', 'cobrada_cliente', 'paga'],
        'paga': [],
    }

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
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2, validators=[validar_positivo])

    condutor_nome = models.CharField('Nome do Condutor', max_length=200, blank=True)
    condutor_cpf = models.CharField('CPF do Condutor', max_length=14, blank=True)

    situacao = models.CharField('Situacao', max_length=30, choices=SITUACAO, default='pendente_identificacao')
    observacoes = models.TextField('Observacoes', blank=True)
    parcela = models.OneToOneField(
        'contracts.ParcelaContrato', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Cobrança no contrato', related_name='multa_transito',
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    PRAZO_COBRANCA_DIAS = 7

    class Meta:
        verbose_name = 'Multa de Trânsito'
        verbose_name_plural = 'Multas de Trânsito'
        ordering = ['-data_infracao']

    def __str__(self):
        return f'{self.veiculo.placa} - {self.data_infracao.strftime("%d/%m/%Y")} - R$ {self.valor}'

    @staticmethod
    def contratos_possiveis(veiculo_id, data_infracao):
        from django.db.models import Q
        from apps.contracts.models import Contrato
        if not veiculo_id or not data_infracao:
            return Contrato.objects.none()
        return Contrato.objects.filter(
            veiculo_id=veiculo_id,
            data_saida__date__lte=data_infracao,
        ).filter(
            Q(situacao='ativo')
            | Q(situacao__in=['aguardando_devolucao', 'encerrado'], data_devolucao_real__date__gte=data_infracao)
        ).select_related('cliente').order_by('data_saida')

    def save(self, *args, **kwargs):
        if not self.contrato_id:
            possiveis = list(self.contratos_possiveis(self.veiculo_id, self.data_infracao)[:2])
            if len(possiveis) == 1:
                self.contrato = possiveis[0]
        super().save(*args, **kwargs)
        self.sincronizar_cobranca()

    @property
    def proximas_situacoes(self):
        rotulos = dict(self.SITUACAO)
        return [(s, rotulos[s]) for s in self.TRANSICOES.get(self.situacao, [])]

    def erro_ao_mudar_para(self, nova):
        if nova not in self.TRANSICOES.get(self.situacao, []):
            return 'Mudança de situação não permitida.'
        if nova == 'identificada' and not self.condutor_nome:
            return 'Informe o nome do condutor em "Editar" antes de marcar como identificada.'
        if nova == 'cobrada_cliente' and not self.contrato_id:
            return 'Esta multa não está ligada a um contrato — não há de quem cobrar.'
        return None

    def sincronizar_cobranca(self):
        from apps.contracts.models import ParcelaContrato
        if not self.contrato_id:
            return
        parcela = self.parcela
        cobrar = self.situacao == 'cobrada_cliente'
        if parcela is None:
            if not cobrar:
                return
            vencimento = timezone.localdate() + timedelta(days=self.PRAZO_COBRANCA_DIAS)
            parcela = ParcelaContrato.objects.create(
                contrato=self.contrato,
                numero=(self.contrato.parcelas.aggregate(m=Max('numero'))['m'] or 0) + 1,
                tipo='multa', data_vencimento=vencimento, valor=self.valor,
                observacoes=f'Multa de trânsito de {self.data_infracao:%d/%m/%Y}'
                            + (f' — auto {self.numero_auto}' if self.numero_auto else ''),
            )
            MultaTransito.objects.filter(pk=self.pk).update(parcela=parcela)
            self.parcela = parcela
            conta = ContaReceber.objects.filter(contrato_id=self.contrato_id).first()
            if conta and conta.data_vencimento < vencimento:
                ContaReceber.sincronizar(self.contrato_id, novo_vencimento=vencimento)
            return
        if parcela.situacao == 'pago':
            return
        if cobrar:
            alterou = parcela.situacao == 'cancelada' or parcela.valor != self.valor
            parcela.situacao = 'pendente' if parcela.situacao == 'cancelada' else parcela.situacao
            parcela.valor = self.valor
        elif self.situacao != 'paga':
            alterou = parcela.situacao != 'cancelada'
            parcela.situacao = 'cancelada'
        else:
            alterou = False
        if alterou:
            parcela.save(update_fields=['situacao', 'valor'])

    @property
    def prazo_critico(self):
        if self.prazo_indicacao and self.situacao == 'pendente_identificacao':
            delta = self.prazo_indicacao - timezone.localdate()
            return delta.days <= 5
        return False

    @property
    def prazo_vencido(self):
        if self.prazo_indicacao:
            return self.prazo_indicacao < timezone.localdate()
        return False


class ContaReceberQuerySet(models.QuerySet):
    def com_parcelas_atrasadas(self, hoje):
        from apps.contracts.models import ParcelaContrato
        atrasadas = (
            ParcelaContrato.objects
            .filter(ParcelaContrato.q_atrasadas(hoje), contrato=OuterRef('contrato'))
            .exclude(tipo='caucao')
            .order_by()
            .values('contrato')
        )
        return self.annotate(
            atrasada_desde=Subquery(atrasadas.annotate(d=Min('data_vencimento')).values('d')),
            qtd_parcelas_atrasadas=Coalesce(
                Subquery(atrasadas.annotate(n=Count('pk')).values('n')), 0,
            ),
            valor_parcelas_atrasadas=Coalesce(
                Subquery(atrasadas.annotate(s=Sum('valor')).values('s')),
                Value(Decimal('0.00')),
                output_field=models.DecimalField(max_digits=10, decimal_places=2),
            ),
        )


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

    objects = ContaReceberQuerySet.as_manager()

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
            self.data_vencimento < timezone.localdate()
            and self.situacao not in ('pago', 'cancelado')
        )

    @property
    def tem_atraso(self):
        if self.situacao in ('pago', 'cancelado'):
            return False
        return self.vencida or getattr(self, 'qtd_parcelas_atrasadas', 0) > 0

    @property
    def valor_vencido(self):
        if self.situacao in ('pago', 'cancelado'):
            return Decimal('0.00')
        saldo = max(self.valor_saldo, Decimal('0.00'))
        if self.vencida:
            return saldo
        return min(getattr(self, 'valor_parcelas_atrasadas', Decimal('0.00')), saldo)

    @classmethod
    def anexar_encargos(cls, contas, hoje=None):
        from apps.contracts.models import ParcelaContrato
        contas = list(contas)
        hoje = hoje or timezone.localdate()
        por_contrato = {c.contrato_id: Decimal('0.00') for c in contas}
        if por_contrato:
            config = ConfiguracaoLocadora.obter()
            atrasadas = ParcelaContrato.objects.filter(
                ParcelaContrato.q_atrasadas(hoje), contrato_id__in=por_contrato,
            ).exclude(tipo__in=ParcelaContrato.TIPOS_SEM_ENCARGOS)
            for parcela in atrasadas:
                por_contrato[parcela.contrato_id] += parcela.valor_corrigido_com(config) - parcela.valor
        for conta in contas:
            conta.encargos_atraso = (
                Decimal('0.00') if conta.situacao in ('pago', 'cancelado') else por_contrato[conta.contrato_id]
            )
        return contas

    @property
    def encargos_atraso(self):
        if '_encargos_atraso' not in self.__dict__:
            self.anexar_encargos([self])
        return self._encargos_atraso

    @encargos_atraso.setter
    def encargos_atraso(self, valor):
        self._encargos_atraso = valor

    @property
    def valor_atualizado(self):
        if self.situacao in ('pago', 'cancelado'):
            return Decimal('0.00')
        return max(self.valor_saldo, Decimal('0.00')) + self.encargos_atraso

    @property
    def valor_vencido_atualizado(self):
        return self.valor_vencido + self.encargos_atraso

    @property
    def dias_em_atraso(self):
        if self.vencida:
            return (timezone.localdate() - self.data_vencimento).days
        return 0

    @property
    def dias_atraso_efetivo(self):
        if not self.tem_atraso:
            return 0
        datas = [d for d in (
            self.data_vencimento if self.vencida else None,
            getattr(self, 'atrasada_desde', None),
        ) if d]
        return (timezone.localdate() - min(datas)).days if datas else 0

    @classmethod
    def resumo_aberto(cls, hoje=None):
        hoje = hoje or timezone.localdate()
        abertas = list(
            cls.objects.exclude(situacao__in=['pago', 'cancelado'])
            .com_parcelas_atrasadas(hoje)
            .select_related('cliente', 'contrato__veiculo')
            .order_by('data_vencimento')
        )
        cls.anexar_encargos(abertas, hoje)
        em_atraso = [c for c in abertas if c.tem_atraso]
        return {
            'abertas': abertas,
            'em_atraso': em_atraso,
            'total_aberto': sum((c.valor_saldo for c in abertas), Decimal('0.00')),
            'total_vencido': sum((c.valor_vencido for c in em_atraso), Decimal('0.00')),
        }

    @property
    def dias_para_vencer(self):
        if self.situacao in ('pago', 'cancelado'):
            return None
        return (self.data_vencimento - timezone.localdate()).days

    @property
    def cor_situacao(self):
        return self.COR_SITUACAO.get(self.situacao, ('#94a3b8', '#f8fafc'))

    @classmethod
    def sincronizar(cls, contrato_id, novo_vencimento=None):
        from django.db import transaction
        with transaction.atomic():
            conta = cls.objects.select_for_update().filter(contrato_id=contrato_id).first()
            if conta is not None:
                conta.atualizar_situacao(novo_vencimento=novo_vencimento)

    def atualizar_situacao(self, novo_vencimento=None):
        if self.situacao == 'cancelado':
            return

        from django.db import transaction
        from apps.contracts.models import Contrato
        with transaction.atomic():
            obj = ContaReceber.objects.select_for_update().get(pk=self.pk)
            if obj.situacao == 'cancelado':
                return

            contrato = Contrato.objects.get(pk=obj.contrato_id)
            obj.valor_total = contrato.total_geral
            obj.valor_pago = contrato.total_pago
            if novo_vencimento is not None:
                obj.data_vencimento = novo_vencimento

            saldo = obj.valor_total - obj.valor_pago
            if saldo <= Decimal('0.00'):
                obj.situacao = 'pago'
            elif obj.data_vencimento < timezone.localdate():
                obj.situacao = 'vencido'
            elif obj.valor_pago > Decimal('0.00'):
                obj.situacao = 'pago_parcial'
            else:
                obj.situacao = 'pendente'

            obj.save(update_fields=[
                'situacao', 'valor_total', 'valor_pago', 'data_vencimento', 'atualizado_em',
            ])
            self.situacao = obj.situacao
            self.valor_total = obj.valor_total
            self.valor_pago = obj.valor_pago
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
            self.situacao == 'pendente' and self.data_vencimento < timezone.localdate()
        )

    @property
    def dias_atraso(self):
        if self.em_atraso:
            return (timezone.localdate() - self.data_vencimento).days
        return 0


def _adicionar_meses(data_base, meses):
    """Soma N meses a data_base respeitando fim de mês (sem dependência extra)."""
    total_mes = data_base.month + meses
    ano = data_base.year + (total_mes - 1) // 12
    mes = (total_mes - 1) % 12 + 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(data_base.day, ultimo_dia))


def cobrancas_gestao_consideradas():
    from apps.investidores.models import CobrancaGestao
    if not ConfiguracaoLocadora.modulo_investidores_ativo():
        return CobrancaGestao.objects.none()
    return CobrancaGestao.objects.all()


def resumo_contas_a_pagar(hoje=None):
    hoje = hoje or timezone.localdate()

    def soma(itens, campo_data, filtro):
        return sum((i.valor for i in itens if filtro(getattr(i, campo_data))), Decimal('0.00'))

    parcelas = list(ParcelaDespesa.objects.filter(situacao__in=['pendente', 'em_atraso']))
    avulsas = list(
        DespesaOperacional.objects.filter(parcelado=False, data_pagamento__isnull=True)
        .select_related('veiculo').order_by('data_competencia')
    )
    repasses = list(
        cobrancas_gestao_consideradas().filter(situacao='pendente')
        .select_related('veiculo_investidor__investidor', 'veiculo_investidor__veiculo')
        .order_by('data_vencimento')
    )
    no_mes = lambda d: d.year == hoje.year and d.month == hoje.month
    vencida = lambda d: d < hoje
    return {
        'avulsas': avulsas,
        'repasses': repasses,
        'total_mes': (
            soma(parcelas, 'data_vencimento', no_mes)
            + soma(avulsas, 'data_competencia', no_mes)
            + soma(repasses, 'data_vencimento', no_mes)
        ),
        'total_vencido': (
            soma(parcelas, 'data_vencimento', vencida)
            + soma(avulsas, 'data_competencia', vencida)
            + soma(repasses, 'data_vencimento', vencida)
        ),
        'total_pendente': (
            sum((p.valor for p in parcelas), Decimal('0.00'))
            + sum((d.valor for d in avulsas), Decimal('0.00'))
            + sum((r.valor for r in repasses), Decimal('0.00'))
        ),
    }


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
    hoje = timezone.localdate()

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


class NotificacaoCobranca(models.Model):
    CANAL = [
        ('whatsapp', 'WhatsApp'),
        ('email', 'E-mail'),
    ]
    ETAPA = [
        ('d_menos_1', 'Vence amanhã'),
        ('d0', 'Vence hoje'),
        ('d_mais_1', '1 dia de atraso'),
        ('d_mais_3', '3 dias de atraso'),
        ('d_mais_7', '7 dias de atraso'),
    ]
    DIAS_ETAPA = {'d_menos_1': -1, 'd0': 0, 'd_mais_1': 1, 'd_mais_3': 3, 'd_mais_7': 7}

    parcela = models.ForeignKey(
        'contracts.ParcelaContrato', on_delete=models.CASCADE,
        verbose_name='Parcela', related_name='notificacoes'
    )
    canal = models.CharField('Canal', max_length=10, choices=CANAL)
    etapa = models.CharField('Etapa', max_length=10, choices=ETAPA)
    destino = models.CharField('Destino', max_length=254, blank=True)
    valor = models.DecimalField('Valor cobrado (R$)', max_digits=10, decimal_places=2)
    enviado_em = models.DateTimeField('Enviado em', default=timezone.now)
    enviado_por = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Enviado por'
    )

    class Meta:
        verbose_name = 'Notificação de Cobrança'
        verbose_name_plural = 'Notificações de Cobrança'
        ordering = ['-enviado_em']
        constraints = [
            models.UniqueConstraint(
                fields=['parcela', 'canal', 'etapa'], name='notificacao_unica_por_etapa'
            ),
        ]

    def __str__(self):
        return f'{self.parcela} - {self.get_canal_display()} - {self.get_etapa_display()}'

    @classmethod
    def etapa_do_dia(cls, data_vencimento, hoje=None):
        """Etapa mais recente da régua já alcançada; etapas puladas não são cobradas."""
        dias = ((hoje or timezone.localdate()) - data_vencimento).days
        alcancadas = [etapa for etapa, offset in cls.DIAS_ETAPA.items() if offset <= dias]
        return alcancadas[-1] if alcancadas else None

    @classmethod
    def a_contatar(cls, canal, parcelas, hoje=None):
        """Parcelas em aberto com etapa da régua pendente no canal.

        Retorna (parcela, etapa, notificacao_de_hoje); notificacao_de_hoje vem
        preenchida quando a etapa já foi enviada hoje, para o painel marcar como cobrada.
        """
        hoje = hoje or timezone.localdate()
        com_etapa = [
            (p, cls.etapa_do_dia(p.data_vencimento, hoje))
            for p in parcelas if p.em_aberto
        ]
        com_etapa = [(p, etapa) for p, etapa in com_etapa if etapa]
        enviadas = {
            (n.parcela_id, n.etapa): n
            for n in cls.objects.filter(
                canal=canal, parcela__in=[p for p, _ in com_etapa]
            )
        }
        resultado = []
        for parcela, etapa in com_etapa:
            enviada = enviadas.get((parcela.pk, etapa))
            if enviada and timezone.localdate(enviada.enviado_em) != hoje:
                continue
            resultado.append((parcela, etapa, enviada))
        return resultado

    @classmethod
    def mensagem(cls, parcela, etapa, link, pix=None):
        from apps.core.templatetags.core_tags import brl
        primeiro_nome = parcela.contrato.cliente.nome_exibicao.split(' ')[0]
        descricao, referente = {
            'caucao': ('caução', 'caução (garantia da locação)'),
            'multa': ('multa de trânsito', 'multa de trânsito do período da locação'),
        }.get(parcela.tipo, (f'parcela {parcela.numero}', f'parcela {parcela.numero} do aluguel'))
        aviso_encargos = (
            'Regularize assim que possível.' if parcela.tipo in parcela.TIPOS_SEM_ENCARGOS
            else 'Regularize para evitar novos encargos.'
        )
        placa = parcela.contrato.veiculo.placa
        vencimento = parcela.data_vencimento.strftime('%d/%m/%Y')
        valor = brl(parcela.valor_corrigido)
        dias = parcela.dias_atraso
        if parcela.valor_corrigido != parcela.valor:
            valor_atualizado = f'Valor atualizado com multa e juros: {valor}.'
        else:
            valor_atualizado = f'Valor: {valor}.'
        atraso = 'venceu ontem' if dias == 1 else f'está em atraso há {dias} dias'

        abertura = {
            'd_menos_1': (
                f'Olá, {primeiro_nome}! Passando para lembrar que a {referente} '
                f'do veículo {placa} vence amanhã ({vencimento}), no valor de {valor}.'
            ),
            'd0': (
                f'Olá, {primeiro_nome}! Hoje ({vencimento}) vence a {referente} '
                f'do veículo {placa}, no valor de {valor}.'
            ),
            'd_mais_1': (
                f'Olá, {primeiro_nome}! Ainda não identificamos o pagamento da {descricao} '
                f'do veículo {placa}, que {atraso} (vencimento em {vencimento}). {valor_atualizado}'
            ),
            'd_mais_3': (
                f'Olá, {primeiro_nome}. A {descricao} do veículo {placa} está em atraso há '
                f'{dias} dias (venceu em {vencimento}). {valor_atualizado} '
                f'{aviso_encargos}'
            ),
            'd_mais_7': (
                f'Olá, {primeiro_nome}. A {descricao} do veículo {placa} está em atraso há '
                f'{dias} dias (venceu em {vencimento}). {valor_atualizado} '
                f'Precisamos que a regularização seja feita com urgência. '
                f'Se estiver com alguma dificuldade, fale com a gente.'
            ),
        }[etapa]

        partes = [abertura, f'Pague pelo Pix neste link:\n{link}']
        if pix:
            partes.append(f'Ou use o Pix copia e cola:\n{pix}')
        partes.append('Depois é só enviar o comprovante por aqui. Obrigado!')
        return '\n\n'.join(partes)
