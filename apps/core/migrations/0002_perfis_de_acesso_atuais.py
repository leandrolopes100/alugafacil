from django.contrib.auth.management import create_permissions
from django.db import migrations

TODAS = (
    'clientes_acessar', 'clientes_excluir_cnh',
    'contratos_consultar', 'contratos_operar', 'contratos_receber', 'contratos_administrar',
    'frota_acessar', 'frota_tarifas', 'frota_excluir', 'frota_financeiro',
    'manutencao_acessar',
    'financeiro_acessar', 'financeiro_excluir_despesa',
    'relatorios_contratos', 'relatorios_frota', 'relatorios_clientes', 'relatorios_financeiros',
    'admin_configuracoes', 'admin_usuarios',
)

PERFIS = {
    'admin_locadora': ('Administrador', 'Acesso total ao sistema', TODAS),
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


def _nome_livre(PerfilAcesso, nome):
    candidato, n = nome[:80], 2
    while PerfilAcesso.objects.filter(nome__iexact=candidato).exists():
        sufixo = f' ({n})'
        candidato, n = nome[:80 - len(sufixo)] + sufixo, n + 1
    return candidato


def criar_perfis(apps, schema_editor):
    app_config = apps.get_app_config('core')
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    PerfilAcesso = apps.get_model('core', 'PerfilAcesso')
    permissoes = {
        p.codename: p for p in Permission.objects.filter(
            content_type__app_label='core', content_type__model='perfilacesso', codename__in=TODAS,
        )
    }
    faltando = set(TODAS) - set(permissoes)
    if faltando:
        raise RuntimeError(f'Permissões não criadas: {sorted(faltando)}')

    for nome_grupo, (nome, descricao, codigos) in PERFIS.items():
        grupo, _ = Group.objects.get_or_create(name=nome_grupo)
        PerfilAcesso.objects.create(grupo=grupo, nome=_nome_livre(PerfilAcesso, nome), descricao=descricao)
        grupo.permissions.add(*[permissoes[c] for c in codigos])

    for grupo in Group.objects.filter(perfil__isnull=True).order_by('pk'):
        PerfilAcesso.objects.create(grupo=grupo, nome=_nome_livre(PerfilAcesso, grupo.name))


def remover_perfis(apps, schema_editor):
    Permission = apps.get_model('auth', 'Permission')
    PerfilAcesso = apps.get_model('core', 'PerfilAcesso')
    PerfilAcesso.objects.all().delete()
    Permission.objects.filter(content_type__app_label='core', content_type__model='perfilacesso').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(criar_perfis, remover_perfis),
    ]
