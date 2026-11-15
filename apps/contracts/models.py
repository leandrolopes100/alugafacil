import math
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import cached_property

from django.contrib.postgres.indexes import GinIndex
from django.core import signing
from django.db import models
from django.db.models import Max, Q, Sum
from django.urls import reverse
from django.utils import timezone

from apps.core.utils import comprime_imagem
from apps.core.validators import validar_nao_negativo, validar_positivo, validar_quantidade_minima

FORMA_ABATIDO_CAUCAO = 'abatido_caucao'

SALT_TOKEN_COBRANCA = 'contracts.parcela.cobranca'
VALIDADE_LINK_COBRANCA = timedelta(days=60)

# Mapeamento de nivel de combustivel para percentual do tanque.
# Usado em calcular_fechamento() para determinar a diferenca de combustivel.
_NIVEL_COMBUSTIVEL = {'vazio': 0, '1/4': 25, '1/2': 50, '3/4': 75, 'cheio': 100}


class Reserva(models.Model):
    CANAL = [
        ('balcao', 'Balcão'),
        ('telefone', 'Telefone'),
        ('whatsapp', 'WhatsApp'),
        ('site', 'Site'),
        ('app', 'Aplicativo'),
    ]
    SITUACAO = [
        ('pendente', 'Pendente'),
        ('confirmada', 'Confirmada'),
        ('ativa', 'Ativa'),
        ('concluida', 'Concluída'),
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
    FORMA_COBRANCA = [
        ('semanal', 'Semanal'),
        ('mensal', 'Mensal'),
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
    valor_km_excedente = models.DecimalField(
        'Valor km excedente (R$)', max_digits=6, decimal_places=2, default=0, validators=[validar_nao_negativo],
    )
    forma_cobranca = models.CharField('Forma de cobrança', max_length=10, choices=FORMA_COBRANCA, default='semanal')
    valor_semanal = models.DecimalField('Pacote semanal (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    valor_mensal = models.DecimalField('Pacote mensal (R$)', max_digits=10, decimal_places=2, null=True, blank=True)

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
                ano = timezone.localdate().year
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
        return self.situacao == 'ativo' and timezone.now() > self.data_devolucao_prevista

    @property
    def devolucao_vencida(self):
        return (
            self.situacao == 'ativo'
            and timezone.localdate() > timezone.localdate(self.data_devolucao_prevista)
        )

    @property
    def dias_devolucao_vencida(self):
        if not self.devolucao_vencida:
            return 0
        return (timezone.localdate() - timezone.localdate(self.data_devolucao_prevista)).days

    @property
    def valor_sem_cobranca_estimado(self):
        if not self.devolucao_vencida:
            return Decimal('0.00')
        return self.valor_periodo(timezone.localdate(self.data_devolucao_prevista), timezone.localdate())

    @property
    def semanas_para_regularizar(self):
        return self.dias_devolucao_vencida // 7 + 1

    @property
    def tipo_cobranca(self):
        return self.forma_cobranca

    @property
    def valor_pacote(self):
        return self.valor_mensal if self.forma_cobranca == 'mensal' else self.valor_semanal

    @property
    def usa_pacote(self):
        return bool(self.valor_pacote)

    def valor_base_ciclo(self, dias, completo):
        from apps.fleet.tarifas import valor_base_ciclo
        return valor_base_ciclo(self.diaria, dias, self.valor_pacote, completo)

    def valor_ciclo(self, dias, completo):
        adicionais = (self.diaria_adicionais * dias).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return self.valor_base_ciclo(dias, completo) + adicionais

    def valor_periodo(self, inicio, fim):
        return sum(
            (self.valor_ciclo(dias, completo) for _, dias, completo in ciclos_de_cobranca(inicio, fim, self.forma_cobranca)),
            Decimal('0.00'),
        )

    def ancora_ciclos(self, tipo_cobranca=None):
        if (tipo_cobranca or self.forma_cobranca) != 'mensal' or not self.data_saida:
            return None
        return timezone.localdate(self.data_saida)

    def _inicio_cobranca(self):
        if self.data_saida and not self.origem_retroativa:
            return timezone.localdate(self.data_saida)
        return timezone.localdate(self.criado_em or timezone.now())

    def prorrogacao_gera_atraso(self):
        return timezone.localdate(self.data_devolucao_prevista) < timezone.localdate()

    def simular_prorrogacao_retroativa(self):
        if self.situacao != 'ativo' or not self.prorrogacao_gera_atraso():
            return None
        hoje = timezone.localdate()
        parcelas = [
            ParcelaContrato(
                contrato=self, tipo=self.forma_cobranca, situacao='pendente', data_vencimento=inicio,
                valor=self.valor_ciclo(dias, completo),
            )
            for inicio, dias, completo in ciclos_de_cobranca(
                self.fim_periodo_coberto(), hoje + timedelta(days=40), self.tipo_cobranca, self.ancora_ciclos(),
            )
            if inicio < hoje
        ]
        valor = sum((p.valor for p in parcelas), Decimal('0.00'))
        com_encargos = sum((p.valor_corrigido for p in parcelas), Decimal('0.00'))
        return {
            'quantidade': len(parcelas),
            'valor': valor,
            'encargos': com_encargos - valor,
            'primeiro_vencimento': parcelas[0].data_vencimento if parcelas else None,
        }

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

    CACHE_FINANCEIRO = (
        'valor_locacao_base', 'total_locacao', 'diaria_adicionais', 'total_adicionais',
        'valor_contratado', 'total_avarias', 'parcelas_cobradas', 'lancamentos_avulsos',
        'total_geral', 'total_pago', 'total_caucao_coletado', 'saldo_devedor',
        'composicao_financeira',
    )
    TIPOS_PAGAMENTO_AVULSO = ('regularizacao', 'adicional', 'multa', 'outros')

    def limpar_cache_financeiro(self):
        for attr in self.CACHE_FINANCEIRO:
            self.__dict__.pop(attr, None)

    @cached_property
    def diaria_adicionais(self):
        return sum(
            (a.diaria * a.quantidade for a in self.adicionais.all()),
            Decimal('0.00'),
        )

    @property
    def diaria_com_adicionais(self):
        return self.diaria + self.diaria_adicionais

    @property
    def dias_contratados(self):
        if self.total_dias:
            return max(self.total_dias - self.dias_extras, 0)
        return self._dias_previstos()

    @cached_property
    def valor_locacao_base(self):
        inicio = self._inicio_cobranca()
        return sum(
            (self.valor_base_ciclo(dias, completo) for _, dias, completo in ciclos_de_cobranca(
                inicio, inicio + timedelta(days=self.dias_contratados), self.forma_cobranca,
            )),
            Decimal('0.00'),
        )

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
        return self.diaria_adicionais * self.dias_contratados

    @cached_property
    def valor_contratado(self):
        return self.total_locacao + self.total_adicionais

    @cached_property
    def total_avarias(self):
        return self.avarias.filter(situacao__in=['cobrada', 'paga']).aggregate(
            s=Sum('valor_cobrado')
        )['s'] or Decimal('0.00')

    @cached_property
    def parcelas_cobradas(self):
        return list(
            self.parcelas.exclude(tipo='caucao').exclude(situacao='cancelada')
            .select_related('pagamento_vinculado').order_by('numero')
        )

    @cached_property
    def lancamentos_avulsos(self):
        return {
            r['tipo']: r['s']
            for r in self.pagamentos.filter(tipo__in=self.TIPOS_PAGAMENTO_AVULSO, parcela__isnull=True)
            .values('tipo').annotate(s=Sum('valor'))
        }

    @cached_property
    def total_geral(self):
        if self.situacao == 'aberto':
            return self.valor_contratado + self.total_avarias
        return (
            sum((p.valor_cobrado for p in self.parcelas_cobradas), Decimal('0.00'))
            + self.total_avarias
            + sum(self.lancamentos_avulsos.values(), Decimal('0.00'))
        )

    @cached_property
    def composicao_financeira(self):
        parcelas = self.parcelas_cobradas
        rotulos_avulsos = dict(PagamentoContrato.TIPO)
        ajuste = sum((p.valor_cobrado - p.valor for p in parcelas), Decimal('0.00'))
        itens = [
            ('Locação (parcelas)', sum((p.valor for p in parcelas if p.tipo not in ('acerto', 'multa')), Decimal('0.00'))),
            ('Acerto final', sum((p.valor for p in parcelas if p.tipo == 'acerto'), Decimal('0.00'))),
            ('Multas de trânsito cobradas', sum((p.valor for p in parcelas if p.tipo == 'multa'), Decimal('0.00'))),
            ('Multa/juros de atraso' if ajuste > 0 else 'Descontos concedidos', ajuste),
            ('Avarias cobradas', self.total_avarias),
        ]
        itens += [
            (rotulos_avulsos[tipo], valor) for tipo, valor in self.lancamentos_avulsos.items()
        ]
        return [(rotulo, valor) for rotulo, valor in itens if valor]

    @cached_property
    def total_pago(self):
        return self.pagamentos.exclude(tipo='caucao').aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

    @cached_property
    def total_caucao_coletado(self):
        """Caução coletado (depósito — não entra no saldo devedor de locação)."""
        return self.pagamentos.filter(tipo='caucao').aggregate(s=Sum('valor'))['s'] or Decimal('0.00')

    @cached_property
    def saldo_devedor(self):
        return self.total_geral - self.total_pago

    def sincronizar_conta_receber(self, novo_vencimento=None):
        from apps.financeiro.models import ContaReceber
        ContaReceber.sincronizar(self.pk, novo_vencimento=novo_vencimento)

    def parcelas_abertas(self):
        return self.parcelas.filter(situacao__in=['pendente', 'em_atraso']).order_by('data_vencimento')

    def inicio_periodo_nao_usado(self, momento):
        if not isinstance(momento, datetime):
            return momento + timedelta(days=1)
        if not self.data_saida:
            return timezone.localdate(momento) + timedelta(days=1)
        return timezone.localdate(self.data_saida) + timedelta(days=self.dias_usados(momento))

    def parcelas_futuras_abertas(self, momento):
        limite = self.inicio_periodo_nao_usado(momento)
        return self.parcelas_abertas().filter(tipo__in=['semanal', 'mensal']).filter(
            Q(inicio_periodo__gte=limite) | Q(inicio_periodo__isnull=True, data_vencimento__gte=limite)
        )

    def parcelas_devidas_ate(self, momento):
        return self.parcelas_abertas().exclude(
            pk__in=self.parcelas_futuras_abertas(momento).values('pk'),
        ).exclude(tipo='multa', data_vencimento__gt=(
            timezone.localdate(momento) if isinstance(momento, datetime) else momento
        ))

    TIPOS_RECEBIMENTO = ('locacao', 'caucao', 'multa', 'adicional', 'outros')

    def receber_em_ordem(self, valor, forma_pagamento, data_pagamento, usuario=None, observacoes='',
                         parcelas=None, tipo_sobra='locacao'):
        if parcelas is None:
            parcelas = self.parcelas_abertas().exclude(tipo='caucao')
        restante = valor
        quitadas = 0
        for parcela in list(parcelas):
            if restante <= 0:
                break
            parte = min(restante, parcela.valor_corrigido)
            parcela.quitar(parte, forma_pagamento, data_pagamento, usuario, observacoes)
            restante -= parte
            quitadas += 1
        if restante > 0:
            PagamentoContrato.objects.create(
                contrato=self, tipo=tipo_sobra, forma_pagamento=forma_pagamento, valor=restante,
                data_pagamento=data_pagamento, registrado_por=usuario, observacoes=observacoes,
            )
        self.limpar_cache_financeiro()
        return quitadas

    def registrar_recebimento(self, tipo, valor, forma_pagamento, data_pagamento, usuario=None, observacoes=''):
        if tipo not in self.TIPOS_RECEBIMENTO:
            raise ValueError(f'Tipo de recebimento não permitido: {tipo}')
        abertas = self.parcelas_abertas()
        if tipo == 'locacao':
            quitadas = self.receber_em_ordem(
                valor, forma_pagamento, data_pagamento, usuario, observacoes,
            )
        elif tipo in ('multa', 'caucao'):
            quitadas = self.receber_em_ordem(
                valor, forma_pagamento, data_pagamento, usuario, observacoes,
                parcelas=abertas.filter(tipo=tipo), tipo_sobra=tipo,
            )
        else:
            PagamentoContrato.objects.create(
                contrato=self, tipo=tipo, forma_pagamento=forma_pagamento, valor=valor,
                data_pagamento=data_pagamento, registrado_por=usuario, observacoes=observacoes,
            )
            quitadas = 0
        if tipo == 'caucao':
            self.atualizar_situacao_caucao(data_pagamento)
        self.limpar_cache_financeiro()
        return quitadas

    def atualizar_situacao_caucao(self, data_pagamento=None, permitir_rebaixar=False):
        if self.caucao_situacao not in ('pendente', 'pago'):
            return
        self.limpar_cache_financeiro()
        completo = self.caucao_valor > 0 and self.total_caucao_coletado >= self.caucao_valor
        nova = 'pago' if completo else 'pendente'
        if nova == self.caucao_situacao or (nova == 'pendente' and not permitir_rebaixar):
            return
        self.caucao_situacao = nova
        self.caucao_pago_em = (data_pagamento or timezone.now()) if completo else None
        self.save(update_fields=['caucao_situacao', 'caucao_pago_em'])

    def cancelar_parcelas_futuras(self, momento, marca):
        futuras = list(self.parcelas_futuras_abertas(momento))
        for parcela in futuras:
            parcela.situacao = 'cancelada'
            parcela.observacoes = f'{parcela.observacoes} {marca}'.strip()
            parcela.save(update_fields=['situacao', 'observacoes'])
        return futuras

    SITUACOES_COM_ADICIONAIS_EDITAVEIS = ('aberto', 'ativo')

    def remover_adicional(self, adicional, usuario):
        if self.situacao not in self.SITUACOES_COM_ADICIONAIS_EDITAVEIS:
            raise ValueError('Adicionais só podem ser removidos antes do check-in.')
        dados = {
            'tipo': adicional.get_tipo_display(), 'descricao': adicional.descricao,
            'diaria': str(adicional.diaria), 'quantidade': adicional.quantidade,
        }
        adicional.delete()
        alteradas = self.reprecificar_parcelas_futuras() if self.situacao == 'ativo' else 0
        dados['parcelas_recalculadas'] = alteradas
        HistoricoContrato.objects.create(
            contrato=self, usuario=usuario, acao='remocao_adicional',
            situacao_anterior=self.situacao, situacao_nova=self.situacao, dados=dados,
        )
        return alteradas

    def reprecificar_parcelas_futuras(self):
        self.limpar_cache_financeiro()
        alteradas = 0
        for parcela in self.parcelas.filter(
            situacao='pendente', tipo__in=['semanal', 'mensal'],
            data_vencimento__gte=timezone.localdate(), dias__isnull=False,
        ):
            novo_valor = self.valor_ciclo(parcela.dias, parcela.periodo_completo)
            if novo_valor != parcela.valor:
                parcela.valor = novo_valor
                parcela.save(update_fields=['valor'])
                alteradas += 1
        return alteradas

    SITUACOES_CAUCAO_EM_PODER = ('pago', 'retido')
    MARCA_CAUCAO_NAO_COLETADO = '[caução não coletado — cancelado no encerramento]'

    @property
    def caucao_em_poder(self):
        if self.caucao_situacao == 'pendente':
            return self.total_caucao_coletado
        if self.caucao_situacao not in self.SITUACOES_CAUCAO_EM_PODER:
            return Decimal('0.00')
        return self.total_caucao_coletado or self.caucao_valor or Decimal('0.00')

    def _dividas_para_caucao(self, momento=None):
        from apps.financeiro.models import MultaTransito
        parcelas = self.parcelas_abertas().exclude(tipo='caucao')
        if momento:
            parcelas = parcelas.exclude(
                pk__in=self.parcelas_futuras_abertas(momento).values('pk')
            )
        dividas = [
            (
                p,
                'Acerto final' if p.tipo == 'acerto'
                else f'Parcela {p.numero} (venc. {p.data_vencimento:%d/%m/%Y})',
                p.valor_corrigido,
            )
            for p in parcelas
        ]
        dividas += [
            (a, f'Avaria — {a.descricao[:60]}', a.valor_cobrado)
            for a in self.avarias.filter(situacao='cobrada', valor_cobrado__gt=0).order_by('criado_em')
        ]
        dividas += [
            (m, f'Multa de trânsito de {m.data_infracao:%d/%m/%Y}', m.valor)
            for m in MultaTransito.objects.filter(contrato=self, situacao='identificada').order_by('data_infracao')
        ]
        return dividas

    def multas_sem_condutor(self):
        from apps.financeiro.models import MultaTransito
        return list(MultaTransito.objects.filter(contrato=self, situacao='pendente_identificacao').order_by('data_infracao'))

    def plano_abatimento_caucao(self, momento=None):
        caucao = self.caucao_em_poder
        if not caucao:
            return None
        disponivel = caucao
        itens = []
        for objeto, descricao, devido in self._dividas_para_caucao(momento):
            abatido = min(disponivel, devido)
            disponivel -= abatido
            itens.append({
                'objeto': objeto, 'descricao': descricao,
                'devido': devido, 'abatido': abatido, 'restante': devido - abatido,
            })
        return {
            'caucao': caucao,
            'itens': itens,
            'total_abatido': caucao - disponivel,
            'devolver': disponivel,
            'restante': sum((i['restante'] for i in itens), Decimal('0.00')),
            'multas_sem_condutor': self.multas_sem_condutor(),
        }

    def aplicar_caucao_no_encerramento(self, usuario):
        for parcela in self.parcelas_abertas().filter(tipo='caucao'):
            parcela.situacao = 'cancelada'
            parcela.observacoes = f'{parcela.observacoes} {self.MARCA_CAUCAO_NAO_COLETADO}'.strip()
            parcela.save(update_fields=['situacao', 'observacoes'])
        plano = self.plano_abatimento_caucao()
        if plano is None:
            return None
        agora = timezone.now()
        for item in plano['itens']:
            if item['abatido'] > 0:
                self._abater_do_caucao(item['objeto'], item['devido'], item['abatido'], usuario, agora)
        if not plano['devolver']:
            self.caucao_situacao = 'retido'
        elif plano['total_abatido']:
            self.caucao_situacao = 'devolvido_parcial'
        else:
            self.caucao_situacao = 'devolvido'
        self._caucao_valor_devolvido = plano['devolver']
        self.limpar_cache_financeiro()
        return plano

    def _abater_do_caucao(self, objeto, devido, abatido, usuario, agora):
        from apps.financeiro.models import MultaTransito
        observacao = f'Abatido do caução no encerramento do contrato {self.numero}'
        if isinstance(objeto, MultaTransito):
            objeto.situacao = 'cobrada_cliente'
            objeto.save()
            objeto = objeto.parcela
        if isinstance(objeto, ParcelaContrato):
            objeto.quitar(abatido, FORMA_ABATIDO_CAUCAO, agora, usuario, observacao)
            return
        avaria = objeto
        if abatido < devido:
            avaria.valor_cobrado = devido - abatido
            avaria.save(update_fields=['valor_cobrado'])
            avaria = AvariaContrato.objects.create(
                contrato=self, descricao=f'{avaria.descricao[:270]} (parte abatida do caução)',
                localizacao=avaria.localizacao, valor_cobrado=abatido, situacao='paga',
            )
        else:
            avaria.situacao = 'paga'
            avaria.save(update_fields=['situacao'])
        PagamentoContrato.objects.create(
            contrato=self, avaria=avaria, tipo='avaria', forma_pagamento=FORMA_ABATIDO_CAUCAO,
            valor=abatido, data_pagamento=agora, registrado_por=usuario, observacoes=observacao,
        )

    @staticmethod
    def tolerancia_devolucao():
        from apps.financeiro.models import ConfiguracaoLocadora
        return timedelta(minutes=ConfiguracaoLocadora.obter().tolerancia_devolucao_minutos)

    @staticmethod
    def dias_por_ciclo(inicio, fim, tolerancia=timedelta(0)):
        segundos = max((fim - inicio).total_seconds(), 0)
        dias, resto = divmod(segundos, 86400)
        if resto > tolerancia.total_seconds():
            dias += 1
        return max(int(dias), 1)

    def dias_contratados_no_fechamento(self):
        dias = (timezone.localdate(self.data_devolucao_prevista) - timezone.localdate(self.data_saida)).days
        return max(dias, 1)

    def fim_periodo_coberto(self):
        return timezone.localdate(self.data_saida) + timedelta(days=self.dias_contratados_no_fechamento())

    def dias_usados(self, momento, tolerancia=None):
        tolerancia = self.tolerancia_devolucao() if tolerancia is None else tolerancia
        return self.dias_por_ciclo(self.data_saida, momento, tolerancia)

    def fim_periodo_contratado(self):
        if not self.data_saida:
            return None
        return self.data_saida + timedelta(days=self.dias_contratados_no_fechamento())

    def limite_devolucao_sem_extra(self, tolerancia=None):
        if not self.data_saida:
            return None
        tolerancia = self.tolerancia_devolucao() if tolerancia is None else tolerancia
        return self.fim_periodo_contratado() + tolerancia

    def calcular_fechamento(self):
        if not (self.km_saida and self.km_devolucao and self.data_saida and self.data_devolucao_real):
            return

        self.km_total = self.km_devolucao - self.km_saida

        self.total_dias = self.dias_usados(self.data_devolucao_real)
        dias_contratados = self.dias_contratados_no_fechamento()
        self.dias_extras = max(self.total_dias - dias_contratados, 0)
        self.valor_dias_extras = self.diaria_com_adicionais * self.dias_extras

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
        ('seguro_basico', 'Seguro Básico'),
        ('seguro_completo', 'Seguro Completo'),
        ('outros', 'Outros'),
    ]

    contrato = models.ForeignKey(
        Contrato, on_delete=models.CASCADE,
        verbose_name='Contrato', related_name='adicionais'
    )
    tipo = models.CharField('Tipo', max_length=25, choices=TIPO)
    descricao = models.CharField('Descricao', max_length=200, blank=True)
    diaria = models.DecimalField('Diaria (R$)', max_digits=8, decimal_places=2, validators=[validar_positivo])
    quantidade = models.PositiveSmallIntegerField('Quantidade', default=1, validators=[validar_quantidade_minima])

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
        ('saida', 'Saída'),
        ('devolucao', 'Devolução'),
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
    valor_cobrado = models.DecimalField(
        'Valor cobrado (R$)', max_digits=10, decimal_places=2, default=0, validators=[validar_nao_negativo],
    )
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

    SITUACOES_AJUSTAVEIS = ('identificada', 'cobrada', 'isenta')

    @property
    def pode_ajustar(self):
        return self.situacao != 'paga'

    def ajustar(self, valor_cobrado, situacao, usuario):
        if not self.pode_ajustar:
            raise ValueError('Avaria já paga não pode ser alterada.')
        if situacao not in self.SITUACOES_AJUSTAVEIS:
            raise ValueError('Situação inválida.')
        antes = {'valor_cobrado': str(self.valor_cobrado), 'situacao': self.situacao}
        if antes == {'valor_cobrado': str(valor_cobrado), 'situacao': situacao}:
            return False
        self.valor_cobrado = valor_cobrado
        self.situacao = situacao
        self.save(update_fields=['valor_cobrado', 'situacao'])
        HistoricoContrato.objects.create(
            contrato=self.contrato, usuario=usuario, acao='ajuste_avaria',
            situacao_anterior=self.contrato.situacao, situacao_nova=self.contrato.situacao,
            dados={
                'avaria_id': self.pk, 'descricao': self.descricao[:100], 'antes': antes,
                'depois': {'valor_cobrado': str(valor_cobrado), 'situacao': situacao},
            },
        )
        return True

    def save(self, *args, **kwargs):
        if self.foto and not getattr(self.foto, '_committed', True):
            comprimida = comprime_imagem(self.foto)
            if comprimida:
                self.foto = comprimida
        super().save(*args, **kwargs)


class PagamentoContratoQuerySet(models.QuerySet):
    def receita(self):
        return self.exclude(tipo='caucao')

    def entradas_de_caixa(self):
        return self.exclude(forma_pagamento=FORMA_ABATIDO_CAUCAO)


class PagamentoContrato(models.Model):
    FORMA = [
        ('dinheiro', 'Dinheiro'),
        ('pix', 'PIX'),
        ('cartao_credito', 'Cartão de Crédito'),
        ('cartao_debito', 'Cartão de Débito'),
        ('transferencia', 'Transferência Bancária'),
        ('cheque', 'Cheque'),
        (FORMA_ABATIDO_CAUCAO, 'Abatido do caução'),
    ]
    FORMAS_RECEBIMENTO = [f for f in FORMA if f[0] != FORMA_ABATIDO_CAUCAO]
    TIPO = [
        ('locacao', 'Locação'),
        ('caucao', 'Caução'),
        ('regularizacao', 'Regularização pré-sistema'),
        ('adicional', 'Adicional'),
        ('avaria', 'Avaria/Dano'),
        ('multa', 'Multa de Trânsito'),
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

    objects = PagamentoContratoQuerySet.as_manager()

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

    def erro_ao_estornar(self):
        if self.forma_pagamento == FORMA_ABATIDO_CAUCAO:
            return 'Valores abatidos do caução no encerramento não podem ser estornados.'
        if self.tipo == 'caucao' and self.contrato.caucao_situacao not in ('pendente', 'pago'):
            return 'O caução deste contrato já foi devolvido ou retido — o estorno não é possível.'
        return None

    def estornar(self, usuario, motivo):
        from django.db import transaction
        erro = self.erro_ao_estornar()
        if erro:
            raise ValueError(erro)
        contrato = self.contrato
        parcela = getattr(self, 'parcela', None)
        if parcela is None and self.tipo == 'caucao':
            parcela = contrato.parcelas.filter(
                tipo='caucao', situacao='pago', pagamento_vinculado__isnull=True,
            ).first()
        avaria = getattr(self, 'avaria', None)
        marca = (
            f'[pagamento de {self.valor} estornado em {timezone.localdate():%d/%m/%Y}'
            f'{" por " + usuario.get_username() if usuario else ""}: {motivo}]'
        )
        dados = {
            'valor': str(self.valor), 'tipo': self.tipo, 'forma_pagamento': self.forma_pagamento,
            'data_pagamento': timezone.localtime(self.data_pagamento).isoformat(),
            'registrado_por': self.registrado_por.get_username() if self.registrado_por else '',
            'parcela': parcela.pk if parcela else None, 'avaria': avaria.pk if avaria else None,
            'observacoes': self.observacoes,
        }
        with transaction.atomic():
            self.delete()
            if parcela is not None and parcela.situacao == 'pago':
                parcela.situacao = 'pendente'
                parcela.data_pagamento = None
                parcela.forma_pagamento = None
                parcela.observacoes = f'{parcela.observacoes} {marca}'.strip()
                parcela.save(update_fields=['situacao', 'data_pagamento', 'forma_pagamento', 'observacoes'])
            if avaria is not None and avaria.situacao == 'paga':
                avaria.situacao = 'cobrada'
                avaria.save(update_fields=['situacao'])
            if self.tipo == 'caucao':
                contrato.atualizar_situacao_caucao(permitir_rebaixar=True)
            HistoricoContrato.objects.create(
                contrato=contrato, usuario=usuario, acao='estorno_pagamento',
                situacao_anterior=contrato.situacao, situacao_nova=contrato.situacao,
                motivo=motivo, dados=dados,
            )
        contrato.limpar_cache_financeiro()


class ParcelaContrato(models.Model):
    TIPO = [
        ('caucao', 'Caução'),
        ('semanal', 'Semanal'),
        ('mensal', 'Mensal'),
        ('acerto', 'Acerto Final'),
        ('multa', 'Multa de trânsito'),
    ]
    TIPOS_SEM_ENCARGOS = ('caucao', 'multa')
    TIPO_PAGAMENTO = {'caucao': 'caucao', 'multa': 'multa'}
    SITUACAO = [
        ('pendente', 'Pendente'),
        ('pago', 'Pago'),
        ('em_atraso', 'Em Atraso'),
        ('cancelada', 'Cancelada'),
    ]
    FORMA = [
        ('dinheiro', 'Dinheiro'),
        ('pix', 'PIX'),
        ('cartao_credito', 'Cartão de Crédito'),
        ('cartao_debito', 'Cartão de Débito'),
        ('transferencia', 'Transferência Bancária'),
        ('cheque', 'Cheque'),
        (FORMA_ABATIDO_CAUCAO, 'Abatido do caução'),
    ]
    FORMAS_RECEBIMENTO = [f for f in FORMA if f[0] != FORMA_ABATIDO_CAUCAO]
    ORIGEM = [
        ('original', 'Contrato Original'),
        ('prorrogacao', 'Prorrogação'),
    ]

    contrato = models.ForeignKey(
        Contrato, on_delete=models.CASCADE,
        verbose_name='Contrato', related_name='parcelas'
    )
    numero = models.PositiveSmallIntegerField('No')
    tipo = models.CharField('Tipo', max_length=10, choices=TIPO, default='semanal')
    data_vencimento = models.DateField('Vencimento')
    valor = models.DecimalField('Valor (R$)', max_digits=10, decimal_places=2)
    dias = models.PositiveSmallIntegerField('Dias cobrados', null=True, blank=True)
    inicio_periodo = models.DateField('Início do período cobrado', null=True, blank=True)
    periodo_completo = models.BooleanField('Período completo (pacote)', default=False)
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

    MARCA_CANCELADA_CHECKIN = '[cancelada no check-in]'

    @property
    def rotulo(self):
        if self.tipo == 'caucao':
            return 'Caução'
        if self.tipo == 'acerto':
            return 'Acerto Final'
        if self.tipo == 'multa':
            return 'Multa de trânsito'
        return f'Parcela {self.numero}'

    @property
    def tipo_pagamento(self):
        return self.TIPO_PAGAMENTO.get(self.tipo, 'locacao')

    def quitar(self, valor, forma_pagamento, data_pagamento, usuario=None, observacoes=''):
        devido = self.valor_corrigido
        if 0 < valor < devido:
            coberto = (self.valor * valor / devido).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            ParcelaContrato.objects.create(
                contrato=self.contrato,
                numero=(self.contrato.parcelas.aggregate(m=Max('numero'))['m'] or 0) + 1,
                tipo=self.tipo, data_vencimento=self.data_vencimento, inicio_periodo=self.inicio_periodo,
                valor=self.valor - coberto, situacao=self.situacao, origem=self.origem,
                observacoes=f'Restante da {self.rotulo.lower()} após pagamento parcial.',
            )
            self.valor = coberto
        return self._registrar_pagamento(valor, forma_pagamento, data_pagamento, usuario, observacoes)

    def quitar_com_desconto(self, valor, forma_pagamento, data_pagamento, usuario, motivo):
        devido = self.valor_corrigido
        desconto = devido - valor
        percentual = (desconto * 100 / devido).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        observacoes = f'Desconto de {desconto} concedido: {motivo}'
        pagamento = self._registrar_pagamento(valor, forma_pagamento, data_pagamento, usuario, observacoes)
        HistoricoContrato.objects.create(
            contrato=self.contrato, usuario=usuario, acao='desconto',
            situacao_anterior=self.contrato.situacao, situacao_nova=self.contrato.situacao,
            motivo=motivo,
            dados={
                'parcela_id': self.pk, 'parcela': self.rotulo, 'pagamento_id': pagamento.pk,
                'valor_devido': str(devido), 'valor_recebido': str(valor),
                'desconto': str(desconto), 'percentual': str(percentual),
                'forma_pagamento': forma_pagamento,
            },
        )
        return pagamento

    def _registrar_pagamento(self, valor, forma_pagamento, data_pagamento, usuario, observacoes):
        self.situacao = 'pago'
        self.data_pagamento = data_pagamento
        self.forma_pagamento = forma_pagamento
        self.observacoes = f'{self.observacoes} {observacoes}'.strip()
        self.save()
        return PagamentoContrato.objects.create(
            contrato=self.contrato, parcela=self, tipo=self.tipo_pagamento,
            forma_pagamento=forma_pagamento, valor=valor, data_pagamento=data_pagamento,
            registrado_por=usuario, observacoes=observacoes,
        )

    @property
    def valor_cobrado(self):
        if self.situacao == 'pago':
            pagamento = getattr(self, 'pagamento_vinculado', None)
            if pagamento is not None:
                return pagamento.valor
        return self.valor

    @staticmethod
    def q_atrasadas(hoje):
        return Q(situacao='em_atraso') | Q(situacao='pendente', data_vencimento__lt=hoje)

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

    @cached_property
    def valor_corrigido(self):
        """Valor com multa e juros conforme ConfiguracaoLocadora."""
        if self.tipo in self.TIPOS_SEM_ENCARGOS or not (self.em_atraso and self.dias_atraso > 0):
            return self.valor
        from apps.financeiro.models import ConfiguracaoLocadora
        return self.valor_corrigido_com(ConfiguracaoLocadora.obter())

    def valor_corrigido_com(self, config):
        if self.tipo in self.TIPOS_SEM_ENCARGOS or not (self.em_atraso and self.dias_atraso > 0):
            return self.valor
        try:
            dias_efetivos = max(self.dias_atraso - config.dias_carencia, 0)
            if dias_efetivos == 0:
                return self.valor
            multa = self.valor * (config.percentual_multa_atraso / Decimal('100'))
            juros = self.valor * (config.percentual_juros_diario / Decimal('100')) * dias_efetivos
            return (self.valor + multa + juros).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except Exception:
            return self.valor

    @property
    def txid_pix(self):
        from apps.financeiro.pix import normalizar_txid
        return normalizar_txid(f'{self.contrato.numero}P{self.numero:02d}')

    def pix_copia_e_cola(self, config=None):
        from apps.financeiro.models import ConfiguracaoLocadora
        if not self.em_aberto:
            return None
        config = config or ConfiguracaoLocadora.obter()
        return config.gerar_pix(self.valor_corrigido, self.txid_pix)

    @property
    def em_aberto(self):
        return self.situacao in ('pendente', 'em_atraso')

    @property
    def token_cobranca(self):
        return signing.dumps(self.pk, salt=SALT_TOKEN_COBRANCA, compress=True)

    @property
    def caminho_cobranca(self):
        return reverse('cobranca-publica', args=[self.token_cobranca])

    @classmethod
    def obter_por_token_cobranca(cls, token):
        pk = signing.loads(token, salt=SALT_TOKEN_COBRANCA, max_age=VALIDADE_LINK_COBRANCA)
        return cls.objects.select_related('contrato__cliente', 'contrato__veiculo').get(pk=pk)


class HistoricoContrato(models.Model):
    ACAO = [
        ('encerramento_antecipado', 'Encerramento Antecipado'),
        ('cancelamento', 'Cancelamento'),
        ('prorrogacao', 'Prorrogação'),
        ('estorno_pagamento', 'Estorno de Pagamento'),
        ('devolucao_antecipada', 'Devolução Antecipada'),
        ('desconto', 'Desconto em Parcela'),
        ('ajuste_avaria', 'Ajuste de Avaria'),
        ('remocao_adicional', 'Remoção de Adicional'),
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


def dias_locados_por_veiculo(inicio, fim, hoje=None):
    hoje = hoje or timezone.localdate()
    fim_efetivo = min(fim, hoje)
    if fim_efetivo < inicio:
        return {}, 0
    limite = fim_efetivo + timedelta(days=1)
    contratos = Contrato.objects.filter(
        situacao__in=['ativo', 'aguardando_devolucao', 'encerrado'],
        data_saida__isnull=False, data_saida__lt=timezone.make_aware(
            datetime.combine(limite, time.min)
        ),
    ).only('veiculo_id', 'situacao', 'data_saida', 'data_devolucao_real', 'data_devolucao_prevista')
    dias = {}
    for contrato in contratos:
        inicio_locacao = timezone.localdate(contrato.data_saida)
        if contrato.data_devolucao_real:
            fim_locacao = timezone.localdate(contrato.data_devolucao_real)
        elif contrato.situacao == 'encerrado':
            fim_locacao = timezone.localdate(contrato.data_devolucao_prevista)
        else:
            fim_locacao = hoje + timedelta(days=1)
        fim_locacao = max(fim_locacao, inicio_locacao + timedelta(days=1))
        sobreposicao = (min(fim_locacao, limite) - max(inicio_locacao, inicio)).days
        if sobreposicao > 0:
            dias[contrato.veiculo_id] = dias.get(contrato.veiculo_id, 0) + sobreposicao
    dias_periodo = (limite - inicio).days
    return {v: min(d, dias_periodo) for v, d in dias.items()}, dias_periodo


def proximo_vencimento_contratual(data_saida, referencia, tipo_cobranca='semanal'):
    """Primeiro vencimento do cronograma do contrato (ancorado em data_saida)
    que não seja anterior a referencia — usa o mesmo passo de gerar_parcelas(),
    então nunca introduz um ciclo que o contrato não teria.
    """
    ciclo = 0
    while _inicio_do_ciclo(data_saida, ciclo, tipo_cobranca) < referencia:
        ciclo += 1
    return _inicio_do_ciclo(data_saida, ciclo, tipo_cobranca)


def _inicio_do_ciclo(ancora, ciclo, tipo_cobranca):
    from dateutil.relativedelta import relativedelta
    if tipo_cobranca == 'mensal':
        return ancora + relativedelta(months=ciclo)
    return ancora + timedelta(days=7 * ciclo)


def ciclos_de_cobranca(data_inicio, data_fim, tipo_cobranca='semanal', ancora=None):
    if (data_fim - data_inicio).days <= 0:
        return []
    if ancora is None or ancora > data_inicio:
        ancora = data_inicio
    ciclo = 1
    while _inicio_do_ciclo(ancora, ciclo, tipo_cobranca) <= data_inicio:
        ciclo += 1
    inicio_na_borda = _inicio_do_ciclo(ancora, ciclo - 1, tipo_cobranca) == data_inicio
    ciclos = []
    data_atual = data_inicio
    while data_atual < data_fim:
        fim_ciclo = _inicio_do_ciclo(ancora, ciclo, tipo_cobranca)
        fim_periodo = min(fim_ciclo, data_fim)
        completo = inicio_na_borda and fim_periodo == fim_ciclo
        ciclos.append((data_atual, (fim_periodo - data_atual).days, completo))
        data_atual = fim_ciclo
        inicio_na_borda = True
        ciclo += 1
    return ciclos


def gerar_parcelas(contrato, data_inicio, data_fim, origem='original', tipo_cobranca='semanal',
                   vencimento_minimo=None):
    """Gera parcelas de locação para o período indicado.

    tipo_cobranca='semanal': 1 parcela/semana upfront (padrão).
    tipo_cobranca='mensal': 1 parcela/mês upfront.
    Período completo usa o pacote do contrato (valor_semanal/valor_mensal) quando houver;
    dias soltos pagam a diária, limitados ao pacote. Adicionais somam por dia.

    Modelo semanal: semana atual paga upfront, demais a cada 7 dias.
    Contratos < 7 dias → 1 parcela proporcional upfront.
    """
    contrato.limpar_cache_financeiro()
    numero = (contrato.parcelas.aggregate(Max('numero'))['numero__max'] or 0) + 1
    tipo = 'mensal' if tipo_cobranca == 'mensal' else 'semanal'
    novas = []
    ancora = contrato.ancora_ciclos(tipo)
    for inicio_ciclo, dias, completo in ciclos_de_cobranca(data_inicio, data_fim, tipo_cobranca, ancora):
        vencimento = max(inicio_ciclo, vencimento_minimo) if vencimento_minimo else inicio_ciclo
        novas.append(ParcelaContrato(
            contrato=contrato,
            numero=numero,
            tipo=tipo,
            data_vencimento=vencimento,
            valor=contrato.valor_ciclo(dias, completo),
            dias=dias,
            inicio_periodo=inicio_ciclo,
            periodo_completo=completo,
            origem=origem,
        ))
        numero += 1

    if novas:
        ParcelaContrato.objects.bulk_create(novas)
        contrato.limpar_cache_financeiro()
        contrato.sincronizar_conta_receber()
    return len(novas)
