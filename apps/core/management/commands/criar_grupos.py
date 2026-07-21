"""
Cria os grupos de permissao do sistema e associa as permissoes corretas.

Uso:
    python manage.py criar_grupos
"""
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db.models import Q


GRUPOS = {
    'admin_locadora': {
        'descricao': 'Acesso total ao sistema',
        'apps': ['fleet', 'customers', 'contracts', 'financeiro', 'manutencao'],
    },
    'atendente': {
        'descricao': 'Atendimento: clientes, reservas e contratos',
        'apps': ['customers', 'contracts', 'fleet'],
        'excluir_acoes': ['delete'],
    },
    'financeiro': {
        'descricao': 'Modulo financeiro e relatorios',
        'apps': ['financeiro', 'contracts'],
        'excluir_acoes': ['delete'],
    },
    'mecanico': {
        'descricao': 'Modulo de manutencao',
        'apps': ['manutencao', 'fleet'],
        'excluir_acoes': ['delete'],
    },
}


class Command(BaseCommand):
    help = 'Cria grupos de permissao do Aluga Facil'

    def handle(self, *args, **options):
        for nome_grupo, config in GRUPOS.items():
            grupo, criado = Group.objects.get_or_create(name=nome_grupo)
            acao = 'Criado' if criado else 'Atualizado'

            permissoes = Permission.objects.filter(
                content_type__app_label__in=config['apps']
            )
            excluir = config.get('excluir_acoes', [])
            if excluir:
                # startswith no ORM nao aceita tuplas — usa Q para OR entre prefixos
                q_excluir = Q()
                for acao in excluir:
                    q_excluir |= Q(codename__startswith=f'{acao}_')
                permissoes = permissoes.exclude(q_excluir)

            grupo.permissions.set(permissoes)
            self.stdout.write(
                f'  {acao}: {nome_grupo} ({permissoes.count()} permissoes)'
            )

        self.stdout.write(self.style.SUCCESS('\nGrupos criados com sucesso!'))
        self.stdout.write('\nGrupos disponiveis:')
        for nome, cfg in GRUPOS.items():
            self.stdout.write(f'  - {nome}: {cfg["descricao"]}')
