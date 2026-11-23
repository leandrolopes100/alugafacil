from django.core.exceptions import ImproperlyConfigured

MODULOS = (
    ('Clientes', 'bi-people', (
        ('clientes_acessar', 'Ver, cadastrar e editar clientes e CNH'),
        ('clientes_excluir_cnh', 'Excluir CNH de cliente'),
    )),
    ('Contratos', 'bi-file-earmark-text', (
        ('contratos_consultar', 'Ver contratos e imprimir o PDF'),
        ('contratos_operar', 'Criar contrato, check-out, check-in, prorrogar, encerrar, cancelar, adicionais e avarias'),
        ('contratos_receber', 'Registrar pagamentos de parcelas e avarias'),
        ('contratos_administrar', 'Estornar pagamento, quebra de contrato, reverter check-in e contrato retroativo'),
    )),
    ('Frota', 'bi-car-front', (
        ('frota_acessar', 'Ver, cadastrar e editar veículos, fotos, documentos e disponibilidade'),
        ('frota_tarifas', 'Cadastrar e editar grupos, categorias e tarifas'),
        ('frota_excluir', 'Excluir veículos, fotos e documentos'),
        ('frota_financeiro', 'Ver o financeiro de cada veículo (faturou, gastou, resultado)'),
    )),
    ('Manutenção', 'bi-tools', (
        ('manutencao_acessar', 'Ordens de serviço e alertas de manutenção'),
    )),
    ('Financeiro', 'bi-cash-stack', (
        ('financeiro_acessar', 'Agenda de cobranças, contas a receber e a pagar, despesas, fluxo de caixa e multas'),
        ('financeiro_excluir_despesa', 'Excluir despesas'),
    )),
    ('Relatórios', 'bi-bar-chart-line', (
        ('relatorios_contratos', 'Relatório de contratos'),
        ('relatorios_frota', 'Relatório de frota'),
        ('relatorios_clientes', 'Relatório de clientes'),
        ('relatorios_financeiros', 'DRE, inadimplência e exportação em CSV'),
    )),
    ('Administração', 'bi-shield-lock', (
        ('admin_configuracoes', 'Configurações da locadora'),
        ('admin_usuarios', 'Usuários e perfis de acesso (pode dar qualquer permissão, inclusive a si mesmo)'),
    )),
)

PERMISSOES = {codigo: rotulo for _, _, itens in MODULOS for codigo, rotulo in itens}

DEPENDENCIAS = {
    'clientes_excluir_cnh': 'clientes_acessar',
    'contratos_operar': 'contratos_consultar',
    'contratos_receber': 'contratos_consultar',
    'contratos_administrar': 'contratos_consultar',
    'frota_tarifas': 'frota_acessar',
    'frota_excluir': 'frota_acessar',
    'frota_financeiro': 'frota_acessar',
    'financeiro_excluir_despesa': 'financeiro_acessar',
}

PERMISSAO_ADMINISTRAR_ACESSOS = 'admin_usuarios'

RELATORIOS_DO_INDICE = ('relatorios_contratos', 'relatorios_clientes', 'relatorios_financeiros')

PERFIS_PADRAO = {
    'admin_locadora': ('Administrador', 'Acesso total ao sistema', tuple(PERMISSOES)),
    'atendente': ('Atendente', 'Clientes, contratos e frota', (
        'clientes_acessar', 'contratos_consultar', 'contratos_operar', 'contratos_receber',
        'frota_acessar', 'relatorios_contratos', 'relatorios_frota', 'relatorios_clientes',
    )),
    'financeiro': ('Financeiro', 'Financeiro, recebimentos e relatórios financeiros', (
        'contratos_consultar', 'contratos_receber', 'financeiro_acessar',
        'relatorios_contratos', 'relatorios_financeiros',
    )),
    'mecanico': ('Mecânico', 'Frota e manutenção', (
        'frota_acessar', 'manutencao_acessar', 'relatorios_frota',
    )),
}


def codigos(permissao):
    if not permissao:
        raise ImproperlyConfigured('A view precisa declarar a permissão exigida.')
    lista = (permissao,) if isinstance(permissao, str) else tuple(permissao)
    desconhecidas = [codigo for codigo in lista if codigo not in PERMISSOES]
    if desconhecidas:
        raise ImproperlyConfigured(f'Permissão inexistente: {", ".join(desconhecidas)}')
    return lista


def tem_permissao(usuario, permissao):
    return any(usuario.has_perm(f'core.{codigo}') for codigo in codigos(permissao))


def faltando_dependencia(selecionadas):
    selecionadas = set(selecionadas)
    return [
        (codigo, DEPENDENCIAS[codigo]) for codigo in PERMISSOES
        if codigo in selecionadas and DEPENDENCIAS.get(codigo) and DEPENDENCIAS[codigo] not in selecionadas
    ]
