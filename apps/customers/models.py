from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone

from apps.core.utils import comprime_imagem
from apps.core.validators import validar_cpf, validar_cnpj


class Cliente(models.Model):
    TIPO = [
        ('pf', 'Pessoa Fisica'),
        ('pj', 'Pessoa Juridica'),
    ]
    SITUACAO = [
        ('ativo', 'Ativo'),
        ('bloqueado', 'Bloqueado'),
    ]

    tipo = models.CharField('Tipo', max_length=2, choices=TIPO, default='pf')

    # Pessoa Fisica
    nome = models.CharField('Nome completo', max_length=200)
    cpf = models.CharField('CPF', max_length=14, blank=True, validators=[validar_cpf])
    data_nascimento = models.DateField('Data de nascimento', null=True, blank=True)

    # Pessoa Juridica
    razao_social = models.CharField('Razao Social', max_length=200, blank=True)
    cnpj = models.CharField('CNPJ', max_length=18, blank=True, validators=[validar_cnpj])
    contato = models.CharField('Nome do Contato', max_length=200, blank=True)

    # Contato
    email = models.EmailField('E-mail', blank=True)
    telefone = models.CharField('Telefone', max_length=20, blank=True)
    celular = models.CharField('Celular', max_length=20, blank=True)

    # Endereco
    logradouro = models.CharField('Logradouro', max_length=200, blank=True)
    numero = models.CharField('Numero', max_length=10, blank=True)
    complemento = models.CharField('Complemento', max_length=100, blank=True)
    bairro = models.CharField('Bairro', max_length=100, blank=True)
    cidade = models.CharField('Cidade', max_length=100, blank=True)
    estado = models.CharField('Estado', max_length=2, blank=True)
    cep = models.CharField('CEP', max_length=9, blank=True)

    situacao = models.CharField('Situacao', max_length=10, choices=SITUACAO, default='ativo')
    motivo_bloqueio = models.TextField('Motivo do bloqueio', blank=True)
    observacoes = models.TextField('Observacoes', blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'
        ordering = ['nome']
        indexes = [
            models.Index(fields=['cpf']),
            models.Index(fields=['cnpj']),
            models.Index(fields=['nome']),
            models.Index(fields=['situacao']),
            GinIndex(fields=['nome'], name='cliente_nome_trgm_idx', opclasses=['gin_trgm_ops']),
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

    @property
    def bloqueado(self):
        return self.situacao == 'bloqueado'


class CNHCliente(models.Model):
    CATEGORIA = [
        ('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D'), ('e', 'E'),
        ('ab', 'AB'), ('ac', 'AC'), ('ad', 'AD'), ('ae', 'AE'),
    ]

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE,
        verbose_name='Cliente', related_name='cnhs'
    )
    numero = models.CharField('Numero', max_length=11)
    estado_emissor = models.CharField('Estado emissor', max_length=2)
    categoria = models.CharField('Categoria', max_length=3, choices=CATEGORIA)
    validade = models.DateField('Validade')
    primeira_habilitacao = models.DateField('Primeira habilitacao', null=True, blank=True)
    foto_frente = models.ImageField('Foto frente', upload_to='clientes/cnh/', blank=True, null=True)
    foto_verso = models.ImageField('Foto verso', upload_to='clientes/cnh/', blank=True, null=True)
    principal = models.BooleanField('CNH principal', default=True)

    class Meta:
        verbose_name = 'CNH'
        verbose_name_plural = 'CNHs'
        ordering = ['-principal', 'validade']

    def save(self, *args, **kwargs):
        if self.principal and self.cliente_id:
            CNHCliente.objects.filter(
                cliente_id=self.cliente_id, principal=True
            ).exclude(pk=self.pk or 0).update(principal=False)
        for campo in ('foto_frente', 'foto_verso'):
            field = getattr(self, campo)
            if field and not getattr(field, '_committed', True):
                comprimida = comprime_imagem(field)
                if comprimida:
                    setattr(self, campo, comprimida)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.numero} ({self.categoria}) - {self.cliente}'

    @property
    def vencida(self):
        return self.validade < timezone.now().date()
