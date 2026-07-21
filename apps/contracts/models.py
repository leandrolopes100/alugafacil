import math
import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import cached_property

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Max, Sum
from django.utils import timezone

from apps.core.utils import comprime_imagem

# Mapeamento de nivel de combustivel para percentual do tanque.
# Usado em calcular_fechamento() para determinar a diferenca de combustivel.
_NIVEL_COMBUSTIVEL = {'vazio': 0, '1/4': 25, '1/2': 50, '3/4': 75, 'cheio': 100}


class Reserva(models.Model):
    CANAL = [
        ('balcao', 'Balcao'),
        ('telefone', 'Telefone'),
        ('whatsapp', 'WhatsApp'),
        ('site', 'Site'),
        ('app', 'Aplicativo'),
    ]
    SITUACAO = [
        ('pendente', 'Pendente'),
        ('confirmada', 'Confirmada'),
        ('ativa', 'Ativa'),
        ('concluida', 'Concluida'),
        ('cancelada', 'Cancelada'),
        ('no_show', 'No Show'),
    ]

    cliente = models.ForeignKey(
        'customers.Cliente', on_delete=models.PROTECT,
        verbose_name='Cliente', related_name='reservas'
    )
    grupo_veiculo = models.ForeignKey(
        'fleet.GrupoVeiculo', on_delete=models.PROTECT,
        verbose_name='Grupo de veiculo', related_name='reservas'
    )
    veiculo = models.ForeignKey(
        'fleet.Veiculo', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Veiculo', related_name='reservas'
    )

    canal = models.CharField('Canal', max_length=20, choices=CANAL, default='balcao')
    situacao = models.CharField('Situacao', max_length=20, choices=SITUACAO, default='pendente')

    data_retirada = models.DateTimeField('Data/hora de retirada')
    data_devolucao = models.DateTimeField('Data/hora de devolucao prevista')

    diaria_cotada = models.DecimalField('Diaria cotada (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    caucao_cotado = models.DecimalField('Caucao cotado (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    adicionais_cotados = models.JSONField('Adicionais cotados', default=dict, blank=True)

    observacoes = models.TextField('Observacoes', blank=True)
    criado_por = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Criado por'
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Reserva'
        verbose_name_plural = 'Reservas'
        ordering = ['-data_retirada']
        indexes = [
            models.Index(fields=['situacao'], name='reserva_situacao_idx'),
            models.Index(fields=['data_retirada'], name='reserva_data_retirada_idx'),
        ]

    def __str__(self):
        return f'Reserva #{self.pk} - {self.cliente} ({self.data_retirada.strftime("%d/%m/%Y")})'

    @property
    def dias_previstos(self):
        delta = self.data_devolucao - self.data_retirada
        return max(math.ceil(delta.total_seconds() / 86400), 1)

    @property
    def total_cotado(self):
        if self.diaria_cotada:
            return self.diaria_cotada * self.dias_previstos
        return Decimal('0.00')


class Contrato(models.Model):
    SITUACAO = [
        ('aberto', 'Aguardando Saída'),
        ('ativo', 'Ativo'),
        ('aguardando_devolucao', 'Em Fechamento'),
        ('encerrado', 'Encerrado'),
        ('cancelado', 'Cancelado'),
    ]
    COMBUSTIVEL = [
        ('vazio', 'Vazio'),
        ('1/4', '1/4'),
        ('1/2', '1/2'),
        ('3/4', '3/4'),
        ('cheio', 'Cheio'),
    ]
    SITUACAO_CAUCAO = [
        ('pendente', 'Pendente'),
        ('pago', 'Pago'),
        ('devolvido_parcial', 'Devolvido Parcialmente'),
        ('devolvido', 'Devolvido'),
        ('retido', 'Retido'),
    ]
    MOTIVO_ENCERRAMENTO_ANTECIPADO = [
        ('cliente_solicitou', 'Solicitação do Cliente'),
        ('inadimplencia', 'Inadimplência'),
        ('problema_mecanico', 'Problema Mecânico'),
        ('substituicao_veiculo', 'Substituição de Veículo'),
        ('outro', 'Outro'),
    ]
    COR_SITUACAO = {
        'aberto': 'yellow',
        'ativo': 'blue',
        'aguardando_devolucao': 'orange',
        'encerrado': 'green',
        'cancelado': 'gray',
    }

    numero = models.CharField('Numero', max_length=20, unique=True, editable=False)
    sequencia_ano = models.PositiveIntegerField('Sequencia do ano', null=True, blank=True, db_index=True)
    reserva = models.OneToOneField(
        Reserva, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Reserva', related_name='contrato'
    )
    cliente = models.ForeignKey(
        'customers.Cliente', on_delete=models.PROTECT,
        verbose_name='Cliente', related_name='contratos'
    )
    veiculo = models.ForeignKey(
        'fleet.Veiculo', on_delete=models.PROTECT,
        verbose_name='Veiculo', related_name='contratos'
    )
    situacao = models.CharField('Situacao', max_length=25, choices=SITUACAO, default='aberto')
    criado_por = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Criado por'
    )

    # Saida (check-out)
    data_saida = models.DateTimeField('Data/hora de saida', null=True, blank=True)
    km_saida = models.PositiveIntegerField('KM de saida', null=True, blank=True)
    combustivel_saida = models.CharField('Combustivel saida', max_length=5, choices=COMBUSTIVEL, null=True, blank=True)
    obs_saida = models.TextField('Observacoes de saida', blank=True)

    # Devolucao prevista e real
    data_devolucao_prevista = models.DateTimeField('Devolucao prevista')
    data_devolucao_real = models.DateTimeField('Devolucao real', null=True, blank=True)
    km_devolucao = models.PositiveIntegerField('KM de devolucao', null=True, blank=True)
    combustivel_devolucao = models.CharField('Combustivel devolucao', max_length=5, choices=COMBUSTIVEL, null=True, blank=True)
    obs_devolucao = models.TextField('Observacoes de devolucao', blank=True)

    # Tarifas snapshot (imutavel historicamente)
    diaria = models.DecimalField('Diaria (R$)', max_digits=10, decimal_places=2)
    km_franquia_diaria = models.PositiveIntegerField('Franquia km/dia', default=200)
    valor_km_excedente = models.DecimalField('Valor km excedente (R$)', max_digits=6, decimal_places=2, default=0)

    # Caucao
    caucao_valor = models.DecimalField('Valor caucao (R$)', max_digits=10, decimal_places=2, default=0)
    caucao_situacao = models.CharField('Situacao caucao', max_length=20, choices=SITUACAO_CAUCAO, default='pendente')
    caucao_pago_em = models.DateTimeField('Caucao pago em', null=True, blank=True)

    # Calculados no fechamento
    total_dias = models.PositiveSmallIntegerField('Total de dias', null=True, blank=True)
    dias_extras = models.PositiveSmallIntegerField('Dias extras', default=0)
    km_total = models.PositiveIntegerField('KM total rodado', null=True, blank=True)
    km_excedente = models.PositiveIntegerField('KM excedente', default=0)
    valor_km_excedente_total = models.DecimalField('Total KM excedente (R$)', max_digits=10, decimal_places=2, default=0)
    valor_dias_extras = models.DecimalField('Total dias extras (R$)', max_digits=10, decimal_places=2, default=0)
    valor_diferenca_combustivel = models.DecimalField('Diferenca combustivel (R$)', max_digits=8, decimal_places=2, default=0)

    # Assinatura digital
    token_assinatura = models.UUIDField('Token assinatura', default=uuid.uuid4, editable=False)
    assinado_em = models.DateTimeField('Assinado em', null=True, blank=True)
    ip_assinatura = models.GenericIPAddressField('IP assinatura', null=True, blank=True)

    # Migração de contrato pré-existente (veículo já estava com o cliente antes do cadastro)
    origem_retroativa = models.BooleanField('Origem retroativa/migração', default=False)

    # Encerramento antecipado (quebra de contrato — devolução antes do prazo previsto)
    encerramento_antecipado = models.BooleanField('Encerramento antecipado', default=False)
    motivo_encerramento = models.CharField(
        'Motivo do encerramento antecipado', max_length=25,
        choices=MOTIVO_ENCERRAMENTO_ANTECIPADO, blank=True,
    )
    motivo_encerramento_detalhe = models.TextField('Detalhe do motivo', blank=True)
    encerrado_por = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Encerrado por',
        related_name='contratos_encerrados_antecipadamente',
    )
    encerrado_em = models.DateTimeField('Encerrado em', null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Contrato'
        verbose_name_plural = 'Contratos'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['situacao']),
            models.Index(fields=['cliente']),
            models.Index(fields=['veiculo']),
            models.Index(fields=['data_devolucao_prevista']),
            models.Index(fields=['data_saida']),
            GinIndex(fields=['numero'], name='contrato_numero_trgm_idx', opclasses=['gin_trgm_ops']),
        ]

    def __str__(self):
        return f'Contrato {self.numero} - {self.cliente} / {self.veiculo.placa}'

    def save(self, *args, **kwargs):
        if not self.numero:
            from django.db import transaction
            with transaction.atomic():
                ano = timezone.now().year
                ultimo = (
                    Contrato.objects.select_for_update()
                    .filter(criado_em__year=ano)
                    .aggregate(m=Max('sequencia_ano'))['m'] or 0
                )
                self.sequencia_ano = ultimo + 1
                self.numero = f'AF-{ano}-{str(self.sequencia_ano).zfill(4)}'
        super().save(*args, **kwargs)

    @property
    def cor_situacao(self):
        return self.COR_SITUACAO.get(self.situacao, 'gray')

    @property
    def assinado(self):
        return self.assinado_em is not None

    @property
    def em_atraso(self):
        if self.situacao in ('ativo', 'aguardando_devolucao'):
            return timezone.now() > self.data_devolucao_prevista
        return False

    def _dias_previstos(self):
        if self.data_saida and not self.origem_retroativa:
            delta = self.data_devolucao_prevista - self.data_saida
        else:
            # criado_em é imutável (auto_now_add) — garante estimativa estável
            # entre requisições antes do checkout, ao contrário de timezone.now().
            # Contratos retroativos também caem aqui: data_saida é a data real
            # do veículo (para KM/histórico), não o início da cobrança — o
            # período pré-cadastro é resolvido por regularização à parte, não
            # deve ser contado no valor operacional do contrato.
            referencia = self.criado_em or timezone.now()
            delta = self.data_devolucao_prevista - referencia
        return max(math.ceil(delta.total_seconds() / 86400), 1)

    @cached_property
    def valor_locacao_base(self):
        """Diária × dias sem acréscimos (km, dias extras, combustível)."""
        dias = self.total_dias or self._dias_previstos()
        return self.diaria * dias

    @cached_property
    def total_locacao(self):
        return (
            self.valor_locacao_base
            + self.valor_km_excedente_total
            + self.valor_dias_extras
            + self.valor_diferenca_combustivel
        )

    @cached_property
    def total_adicionais(self):
        dias = self.total_dias or self._dias_previstos()
        return sum(
            a.diaria * a.quantidade * dias
            for a in self.adicionais.all()
        )

    @cached_property
    def total_avarias(self):
        # 'paga' também entra no total — o pagamento é registrado via ContratoAvariaMarcarPagaView
        return self.avarias.filter(situacao__in=['cobrada', 'paga']).aggregate(
            s=Sum('valor_cobrado')
        )['s'] or Decimal('0.00')

    @cached_property
    def total_geral(self):
        return self.total_locacao + self.total_adicionais + self.total_avarias

    @cached_property
    def total_pago(self):
        # Exclui caução: é depósito, não pagamento de locação
        return self.pagamentos.exclude(tipo='caucao').aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

    @cached_property
    def total_caucao_coletado(self):
        """Caução coletado (depósito — não entra no saldo devedor de locação)."""
        return self.pagamentos.filter(tipo='caucao').aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

    @cached_property
    def saldo_devedor(self):
        return self.total_geral - self.total_pago

    def calcular_fechamento(self):
        if not (self.km_saida and self.km_devolucao and self.data_saida and self.data_devolucao_real):
            return

        self.km_total = self.km_devolucao - self.km_saida

        # ceil garante que qualquer hora iniciada conta como dia completo (ex: 1d 1h = 2 dias)
        delta_real = self.data_devolucao_real - self.data_saida
        self.total_dias = max(math.ceil(delta_real.total_seconds() / 86400), 1)

        delta_contratado = self.data_devolucao_prevista - self.data_saida
        dias_contratados = max(math.ceil(delta_contratado.total_seconds() / 86400), 1)
        self.dias_extras = max(self.total_dias - dias_contratados, 0)
        self.valor_dias_extras = self.diaria * self.dias_extras

        km_franquia_total = self.km_franquia_diaria * self.total_dias
        self.km_excedente = max(self.km_total - km_franquia_total, 0)
        self.valor_km_excedente_total = self.valor_km_excedente * self.km_excedente

        # Diferenca de combustivel: cobra por cada 1/4 de tanque faltante
        nivel_saida = _NIVEL_COMBUSTIVEL.get(self.combustivel_saida or '', 0)
        nivel_devolucao = _NIVEL_COMBUSTIVEL.get(self.combustivel_devolucao or '', 0)
        diff_pct = nivel_saida - nivel_devolucao
        if diff_pct > 0:
            try:
                from apps.financeiro.models import ConfiguracaoLocadora
                config = ConfiguracaoLocadora.obter()
                if config.custo_reposicao_combustivel > 0:
                    quartos = math.ceil(diff_pct / 25)
                    self.valor_diferenca_combustivel = (
                        Decimal(str(quartos)) * config.custo_reposicao_combustivel
                    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                else:
                    self.valor_diferenca_combustivel = Decimal('0.00')
            except Exception:
                self.valor_diferenca_combustivel = Decimal('0.00')
        else:
            self.valor_diferenca_combustivel = Decimal('0.00')


class AdicionalContrato(models.Model):
    TIPO = [
        ('gps', 'GPS'),
        ('cadeirinha', 'Cadeirinha'),
        ('condutor_adicional', 'Condutor Adicional'),
        ('seguro_basico', 'Seguro Basico'),
        ('seguro_completo', 'Seguro Completo'),
        ('outros', 'Outros'),
    ]

    contrato = models.ForeignKey(
        Contrato, on_delete=models.CASCADE,
        verbose_name='Contrato', related_name='adicionais'
    )
    tipo = models.CharField('Tipo', max_length=25, choices=TIPO)
    descricao = models.CharField('Descricao', max_length=200, blank=True)
    diaria = models.DecimalField('Diaria (R$)', max_digits=8, decimal_places=2)
    quantidade = models.PositiveSmallIntegerField('Quantidade', default=1)

    class Meta:
        verbose_name = 'Adicional'
        verbose_name_plural = 'Adicionais'

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.contrato.numero}'

    @property
    def total(self):
        # Usa dias previstos antes do check-in para não mostrar sempre "1 dia"
        dias = self.contrato.total_dias or self.contrato._dias_previstos()
        return self.diaria * self.quantidade * dias


class FotoContrato(models.Model):
    MOMENTO = [
        ('saida', 'Saida'),
        ('devolucao', 'Devolucao'),
    ]
    POSICAO = [
        ('frente', 'Frente'),
        ('traseira', 'Traseira'),
        ('lateral_esq', 'Lateral esquerda'),
        ('lateral_dir', 'Lateral direita'),
        ('interior_frente', 'Interior frente'),
        ('interior_tras', 'Interior traseira'),
        ('outro', 'Outro'),
    ]

    contrato = models.ForeignKey(
        Contrato, on_delete=models.CASCADE,
        verbose_name='Contrato', related_name='fotos'
    )
    momento = models.CharField('Momento', max_length=10, choices=MOMENTO)
    imagem = models.ImageField('Imagem', upload_to='contratos/fotos/')
    posicao = models.CharField('Posicao', max_length=20, choices=POSICAO, default='outro')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Foto do Contrato'
        verbose_name_plural = 'Fotos do Contrato'
        ordering = ['momento', 'posicao']
        indexes = [
            models.Index(fields=['contrato', 'momento'], name='foto_contrato_momento_idx'),
        ]

    def __str__(self):
        return f'{self.contrato.numero} - {self.get_momento_display()} / {self.get_posicao_display()}'

    def save(self, *args, **kwargs):
        if self.imagem and not getattr(self.imagem, '_committed', True):
            comprimida = comprime_imagem(self.imagem)
            if comprimida:
                self.imagem = comprimida
        super().save(*args, **kwargs)


class AvariaContrato(models.Model):
    SITUACAO = [
        ('identificada', 'Identificada'),
        ('cobrada', 'Cobrada'),
        ('paga', 'Paga'),
        ('isenta', 'Isenta'),
    ]

    contrato = models.ForeignKey(
        Contrato, on_delete=models.CASCADE,
        verbose_name='Contrato', related_name='avarias'
    )
    descricao = models.CharField('Descricao', max_length=300)
    localizacao = models.CharField('Localizacao no veiculo', max_length=100, blank=True)
    valor_cobrado = models.DecimalField('Valor cobrado (R$)', max_digits=10, decimal_places=2, default=0)
    foto = models.ImageField('Foto', upload_to='contratos/avarias/', blank=True, null=True)
    situacao = models.CharField('Situacao', max_length=15, choices=SITUACAO, default='identificada')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Avaria'
        verbose_name_plural = 'Avarias'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['situacao'], name='avaria_situacao_idx'),
        ]

    def __str__(self):
        return f'{self.contrato.numero} - {self.descricao[:50]}'

    def save(self, *args, **kwargs):
        if self.foto and not getattr(self.foto, '_committed', True):
            comprimida = comprime_imagem(self.foto)
            if comprimida:
                self.foto = comprimida
        super().save(*args, **kwargs)


class PagamentoContrato(models.Model):
    FORMA = [
        ('dinheiro', 'Dinheiro'),
        ('pix', 'PIX'),
        ('cartao_credito', 'Cartao de Credito'),
        ('cartao_debito', 'Cartao de Debito'),
        ('transferencia', 'Transferencia Bancaria'),
        ('cheque', 'Cheque'),
    ]
    TIPO = [
        ('locacao', 'Locacao'),
        ('caucao', 'Caucao'),
        ('adicional', 'Adicional'),
        ('avaria', 'Avaria/Dano'),
        ('multa', 'Multa de Transito'),
        ('outros', 'Outros'),
    ]

    contrato = models.ForeignKey(
        Contrato, on_delete=models.CASCADE,
        verbose_name='Contrato', related_name='pagamentos'
    )
    # Vinculo direto com a divida quitada -- opcional pois pagamentos que cobrem
    # varias parcelas/avarias de uma vez (retencao de caucao, baixa FIFO avulsa)
    # nao tem um alvo unico atribuivel. unique=True quando preenchido impede
    # duas baixas para a mesma parcela/avaria (protege contra duplo clique).
    parcela = models.OneToOneField(
        'ParcelaContrato', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='Parcela', related_name='pagamento_vinculado',
    )
    avaria = models.OneToOneField(
        'AvariaContrato', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='Avaria', related_name='pagamento_vinculado',
    )
    forma_pagamento = models.CharField('Forma de Pagamento', max_length=20, choices=FORMA)
    tipo = models.CharField('Referente a', max_length=20, choices=TIPO, default='locacao')
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2)
    data_pagamento = models.DateTimeField('Data/hora do pagamento', default=timezone.now)
    observacoes = models.TextField('Observacoes', blank=True)
    registrado_por = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Registrado por'
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pagamento'
        verbose_name_plural = 'Pagamentos'
        ordering = ['-data_pagamento']
        indexes = [
            models.Index(fields=['data_pagamento'], name='pagamento_data_idx'),
            models.Index(fields=['contrato', 'tipo'], name='pagamento_contrato_tipo_idx'),
        ]

    def __str__(self):
        return f'{self.contrato.numero} - R$ {self.valor} ({self.get_forma_pagamento_display()})'


class ParcelaContrato(models.Model):
    TIPO = [
        ('caucao', 'Caucao'),
        ('semanal', 'Semanal'),
        ('mensal', 'Mensal'),
        ('acerto', 'Acerto Final'),
    ]
    SITUACAO = [
        ('pendente', 'Pendente'),
        ('pago', 'Pago'),
        ('em_atraso', 'Em Atraso'),
        ('cancelada', 'Cancelada'),
    ]
    FORMA = [
        ('dinheiro', 'Dinheiro'),
        ('pix', 'PIX'),
        ('cartao_credito', 'Cartao de Credito'),
        ('cartao_debito', 'Cartao de Debito'),
        ('transferencia', 'Transferencia Bancaria'),
        ('cheque', 'Cheque'),
    ]
    ORIGEM = [
        ('original', 'Contrato Original'),
        ('prorrogacao', 'Prorrogacao'),
    ]

    contrato = models.ForeignKey(
        Contrato, on_delete=models.CASCADE,
        verbose_name='Contrato', related_name='parcelas'
    )
    numero = models.PositiveSmallIntegerField('No')
    tipo = models.CharField('Tipo', max_length=10, choices=TIPO, default='semanal')
    data_vencimento = models.DateField('Vencimento')
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2)
    situacao = models.CharField('Situacao', max_length=15, choices=SITUACAO, default='pendente')
    data_pagamento = models.DateTimeField('Pago em', null=True, blank=True)
    forma_pagamento = models.CharField('Forma', max_length=20, choices=FORMA, null=True, blank=True)
    observacoes = models.TextField('Observacoes', blank=True)
    origem = models.CharField('Origem', max_length=15, choices=ORIGEM, default='original')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Parcela'
        verbose_name_plural = 'Parcelas'
        ordering = ['data_vencimento', 'numero']
        indexes = [
            models.Index(fields=['situacao']),
            models.Index(fields=['data_vencimento']),
            models.Index(fields=['contrato', 'situacao']),
        ]

    def __str__(self):
        return f'{self.contrato.numero} - Parcela {self.numero} ({self.data_vencimento})'

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

    @property
    def valor_corrigido(self):
        """Valor com multa e juros conforme ConfiguracaoLocadora."""
        if not (self.em_atraso and self.dias_atraso > 0):
            return self.valor
        try:
            from apps.financeiro.models import ConfiguracaoLocadora
            config = ConfiguracaoLocadora.obter()
            dias_efetivos = max(self.dias_atraso - config.dias_carencia, 0)
            if dias_efetivos == 0:
                return self.valor
            multa = self.valor * (config.percentual_multa_atraso / Decimal('100'))
            juros = self.valor * (config.percentual_juros_diario / Decimal('100')) * dias_efetivos
            return (self.valor + multa + juros).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except Exception:
            return self.valor


class HistoricoContrato(models.Model):
    ACAO = [
        ('encerramento_antecipado', 'Encerramento Antecipado'),
        ('cancelamento', 'Cancelamento'),
        ('prorrogacao', 'Prorrogação'),
    ]

    contrato = models.ForeignKey(
        Contrato, on_delete=models.CASCADE,
        verbose_name='Contrato', related_name='historico',
    )
    usuario = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Usuario',
    )
    acao = models.CharField('Acao', max_length=30, choices=ACAO)
    situacao_anterior = models.CharField('Situacao anterior', max_length=25, blank=True)
    situacao_nova = models.CharField('Situacao nova', max_length=25, blank=True)
    motivo = models.TextField('Motivo', blank=True)
    dados = models.JSONField('Dados adicionais', default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Historico do Contrato'
        verbose_name_plural = 'Historico dos Contratos'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['contrato', 'criado_em'], name='historico_contrato_data_idx'),
        ]

    def __str__(self):
        return f'{self.contrato.numero} - {self.get_acao_display()} ({self.criado_em:%d/%m/%Y})'


def proximo_vencimento_contratual(data_saida, referencia, tipo_cobranca='semanal'):
    """Primeiro vencimento do cronograma do contrato (ancorado em data_saida)
    que não seja anterior a referencia — usa o mesmo passo de gerar_parcelas(),
    então nunca introduz um ciclo que o contrato não teria.
    """
    from dateutil.relativedelta import relativedelta
    passo = relativedelta(months=1) if tipo_cobranca == 'mensal' else timedelta(days=7)
    data_atual = data_saida
    while data_atual < referencia:
        data_atual += passo
    return data_atual


def gerar_parcelas(contrato, data_inicio, data_fim, origem='original', tipo_cobranca='semanal'):
    """Gera parcelas de locação para o período indicado.

    tipo_cobranca='semanal': 1 parcela/semana upfront (padrão).
    tipo_cobranca='mensal': 1 parcela/mês upfront, valor = diaria * dias_do_mes.

    Modelo semanal: semana atual paga upfront, demais a cada 7 dias.
    Contratos < 7 dias → 1 parcela proporcional upfront.
    """
    from dateutil.relativedelta import relativedelta
    delta_dias = (data_fim - data_inicio).days
    if delta_dias <= 0:
        return 0

    ultimo_num = contrato.parcelas.aggregate(Max('numero'))['numero__max'] or 0
    numero = ultimo_num + 1
    novas = []

    if tipo_cobranca == 'mensal':
        data_atual = data_inicio
        while data_atual < data_fim:
            proxima = data_atual + relativedelta(months=1)
            fim_periodo = min(proxima, data_fim)
            dias = (fim_periodo - data_atual).days
            valor = (contrato.diaria * dias).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            novas.append(ParcelaContrato(
                contrato=contrato,
                numero=numero,
                tipo='mensal',
                data_vencimento=data_atual,
                valor=valor,
                origem=origem,
            ))
            data_atual = proxima
            numero += 1
    elif delta_dias < 7:
        # Contrato curto: 1 parcela proporcional paga upfront
        valor = (contrato.diaria * delta_dias).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        novas.append(ParcelaContrato(
            contrato=contrato,
            numero=numero,
            tipo='semanal',
            data_vencimento=data_inicio,
            valor=valor,
            origem=origem,
        ))
    else:
        # Primeira semana paga upfront (data_inicio), demais a cada 7 dias;
        # o ultimo ciclo e proporcional aos dias restantes ate data_fim, nunca
        # uma semana cheia quando sobra menos que 7 dias.
        data_atual = data_inicio
        while data_atual < data_fim:
            proxima = data_atual + timedelta(days=7)
            fim_periodo = min(proxima, data_fim)
            dias = (fim_periodo - data_atual).days
            valor = (contrato.diaria * dias).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            novas.append(ParcelaContrato(
                contrato=contrato,
                numero=numero,
                tipo='semanal',
                data_vencimento=data_atual,
                valor=valor,
                origem=origem,
            ))
            data_atual = proxima
            numero += 1

    if novas:
        ParcelaContrato.objects.bulk_create(novas)
    return len(novas)
