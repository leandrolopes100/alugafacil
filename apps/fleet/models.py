from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone

from apps.core.utils import comprime_imagem
from apps.core.validators import validar_placa


class CategoriaVeiculo(models.Model):
    nome = models.CharField('Categoria', max_length=80)
    icone = models.CharField('Icone CSS', max_length=60, blank=True)
    cor = models.CharField('Cor hex', max_length=7, default='#3B82F6')
    ordem = models.PositiveSmallIntegerField('Ordem', default=0)
    ativo = models.BooleanField('Ativo', default=True)

    class Meta:
        verbose_name = 'Categoria de Veiculo'
        verbose_name_plural = 'Categorias de Veiculos'
        ordering = ['ordem', 'nome']

    def __str__(self):
        return self.nome


class GrupoVeiculo(models.Model):
    nome = models.CharField('Nome do grupo', max_length=100)
    categoria = models.ForeignKey(
        CategoriaVeiculo, on_delete=models.PROTECT,
        verbose_name='Categoria', related_name='grupos'
    )
    descricao = models.TextField('Descricao', blank=True)
    diaria = models.DecimalField('Diaria (R$)', max_digits=10, decimal_places=2)
    semanal = models.DecimalField('Semanal (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    mensal = models.DecimalField('Mensal (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    km_franquia_diaria = models.PositiveIntegerField('Franquia km/dia', default=200)
    valor_km_excedente = models.DecimalField('Valor km excedente (R$)', max_digits=6, decimal_places=2, default=0)
    caucao = models.DecimalField('Caucao (R$)', max_digits=10, decimal_places=2, default=0)
    ativo = models.BooleanField('Ativo', default=True)

    class Meta:
        verbose_name = 'Grupo de Veiculo'
        verbose_name_plural = 'Grupos de Veiculos'
        ordering = ['categoria', 'nome']

    def __str__(self):
        return f'{self.nome} ({self.categoria})'


class Veiculo(models.Model):
    COMBUSTIVEL = [
        ('flex', 'Flex'),
        ('gasolina', 'Gasolina'),
        ('etanol', 'Etanol'),
        ('diesel', 'Diesel'),
        ('eletrico', 'Eletrico'),
        ('hibrido', 'Hibrido'),
    ]
    TRANSMISSAO = [
        ('manual', 'Manual'),
        ('automatico', 'Automatico'),
        ('cvt', 'CVT'),
    ]
    SITUACAO = [
        ('disponivel', 'Disponivel'),
        ('reservado', 'Reservado'),
        ('em_uso', 'Em Uso'),
        ('manutencao', 'Em Manutencao'),
        ('inativo', 'Inativo'),
    ]
    COR_SITUACAO = {
        'disponivel': 'green',
        'reservado': 'yellow',
        'em_uso': 'blue',
        'manutencao': 'red',
        'inativo': 'gray',
    }

    placa = models.CharField('Placa', max_length=8, unique=True, validators=[validar_placa])
    chassi = models.CharField('Chassi', max_length=17, blank=True)
    renavam = models.CharField('RENAVAM', max_length=11, blank=True)
    marca = models.CharField('Marca', max_length=50)
    modelo = models.CharField('Modelo', max_length=80)
    ano_fabricacao = models.PositiveSmallIntegerField('Ano fabricacao')
    ano_modelo = models.PositiveSmallIntegerField('Ano modelo')
    cor = models.CharField('Cor', max_length=50)
    combustivel = models.CharField('Combustivel', max_length=20, choices=COMBUSTIVEL, default='flex')
    transmissao = models.CharField('Transmissao', max_length=20, choices=TRANSMISSAO, default='manual')
    portas = models.PositiveSmallIntegerField('Portas', default=4)
    lugares = models.PositiveSmallIntegerField('Lugares', default=5)

    km_atual = models.PositiveIntegerField('KM atual', default=0)
    grupo = models.ForeignKey(
        GrupoVeiculo, on_delete=models.PROTECT,
        verbose_name='Grupo', related_name='veiculos'
    )
    situacao = models.CharField('Situacao', max_length=20, choices=SITUACAO, default='disponivel')

    data_aquisicao = models.DateField('Data de aquisicao', null=True, blank=True)
    valor_aquisicao = models.DecimalField('Valor de aquisicao (R$)', max_digits=12, decimal_places=2, null=True, blank=True)
    valor_fipe = models.DecimalField('Valor FIPE (R$)', max_digits=12, decimal_places=2, null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Veiculo'
        verbose_name_plural = 'Veiculos'
        ordering = ['marca', 'modelo']
        indexes = [
            models.Index(fields=['situacao']),
            GinIndex(fields=['placa'], name='veiculo_placa_trgm_idx', opclasses=['gin_trgm_ops']),
        ]

    def __str__(self):
        return f'{self.placa} - {self.marca} {self.modelo} ({self.ano_modelo})'

    @property
    def disponivel(self):
        return self.situacao == 'disponivel'

    @property
    def cor_situacao(self):
        return self.COR_SITUACAO.get(self.situacao, 'gray')

    @property
    def foto_principal(self):
        return self.fotos.filter(principal=True).first() or self.fotos.first()


class FotoVeiculo(models.Model):
    POSICAO = [
        ('frente', 'Frente'),
        ('traseira', 'Traseira'),
        ('lateral_esq', 'Lateral esquerda'),
        ('lateral_dir', 'Lateral direita'),
        ('interior_frente', 'Interior frente'),
        ('interior_tras', 'Interior traseira'),
        ('outro', 'Outro'),
    ]

    veiculo = models.ForeignKey(Veiculo, on_delete=models.CASCADE, related_name='fotos')
    imagem = models.ImageField('Imagem', upload_to='veiculos/fotos/')
    posicao = models.CharField('Posicao', max_length=20, choices=POSICAO, default='outro')
    legenda = models.CharField('Legenda', max_length=200, blank=True)
    principal = models.BooleanField('Foto principal', default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Foto do Veiculo'
        verbose_name_plural = 'Fotos do Veiculo'
        ordering = ['-principal', 'posicao']

    def __str__(self):
        return f'{self.veiculo.placa} - {self.get_posicao_display()}'

    def save(self, *args, **kwargs):
        if self.principal:
            FotoVeiculo.objects.filter(
                veiculo=self.veiculo, principal=True
            ).exclude(pk=self.pk).update(principal=False)
        if self.imagem and not getattr(self.imagem, '_committed', True):
            comprimida = comprime_imagem(self.imagem)
            if comprimida:
                self.imagem = comprimida
        super().save(*args, **kwargs)


class DocumentoVeiculo(models.Model):
    TIPO = [
        ('crlv', 'CRLV'),
        ('seguro', 'Seguro'),
        ('extintor', 'Extintor'),
        ('vistoria', 'Vistoria'),
        ('outros', 'Outros'),
    ]

    veiculo = models.ForeignKey(Veiculo, on_delete=models.CASCADE, related_name='documentos')
    tipo = models.CharField('Tipo', max_length=20, choices=TIPO)
    numero = models.CharField('Numero', max_length=50, blank=True)
    emissor = models.CharField('Emissor', max_length=100, blank=True)
    data_emissao = models.DateField('Data de emissao', null=True, blank=True)
    data_validade = models.DateField('Validade', null=True, blank=True)
    arquivo = models.FileField('Arquivo', upload_to='veiculos/documentos/', blank=True, null=True)
    dias_alerta = models.PositiveSmallIntegerField('Alertar X dias antes', default=30)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Documento do Veiculo'
        verbose_name_plural = 'Documentos do Veiculo'
        ordering = ['tipo', 'data_validade']

    def __str__(self):
        return f'{self.veiculo.placa} - {self.get_tipo_display()}'

    @property
    def vencido(self):
        if self.data_validade:
            return self.data_validade < timezone.now().date()
        return False

    @property
    def proximo_vencimento(self):
        if self.data_validade:
            delta = self.data_validade - timezone.now().date()
            return 0 <= delta.days <= self.dias_alerta
        return False


class HistoricoKmVeiculo(models.Model):
    ORIGEM = [
        ('contrato_saida', 'Saida de Contrato'),
        ('contrato_devolucao', 'Devolucao de Contrato'),
        ('manutencao', 'Manutencao'),
        ('manual', 'Atualizacao Manual'),
    ]

    veiculo = models.ForeignKey(
        Veiculo, on_delete=models.CASCADE,
        verbose_name='Veiculo', related_name='historico_km'
    )
    km = models.PositiveIntegerField('KM')
    data = models.DateTimeField('Data/hora', default=timezone.now)
    origem = models.CharField('Origem', max_length=25, choices=ORIGEM)
    contrato = models.ForeignKey(
        'contracts.Contrato', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Contrato', related_name='historico_km'
    )
    observacao = models.CharField('Observacao', max_length=200, blank=True)
    registrado_por = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Historico de KM'
        verbose_name_plural = 'Historico de KM'
        ordering = ['-data']
        indexes = [
            models.Index(fields=['veiculo', 'data']),
        ]

    def __str__(self):
        return f'{self.veiculo.placa} - {self.km} km ({self.get_origem_display()})'
