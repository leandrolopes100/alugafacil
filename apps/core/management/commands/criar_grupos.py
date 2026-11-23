from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import PerfilAcesso
from apps.core.permissoes import PERFIS_PADRAO


class Command(BaseCommand):
    help = (
        'Cria os perfis de acesso padrão (Administrador, Atendente, Financeiro, Mecânico) que estiverem faltando. '
        'Perfis existentes não são alterados, a menos que se use --redefinir.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--redefinir', action='store_true',
            help='Volta as permissões dos 4 perfis padrão para o original, desfazendo o que foi mudado na tela.',
        )

    @transaction.atomic
    def handle(self, *args, **opcoes):
        for nome_grupo, (nome, descricao, codigos) in PERFIS_PADRAO.items():
            grupo, criado = Group.objects.get_or_create(name=nome_grupo)
            perfil = grupo.perfil
            if criado:
                perfil.nome = PerfilAcesso.nome_livre(nome, exceto=perfil.pk)
                perfil.descricao = descricao
                perfil.save()
            if criado or opcoes['redefinir']:
                perfil.definir_permissoes(codigos)
                self.stdout.write(f'  {"Criado" if criado else "Redefinido"}: {perfil.nome} ({len(codigos)} permissões)')
            else:
                self.stdout.write(f'  Mantido: {perfil.nome}')
        self.stdout.write(self.style.SUCCESS('Perfis padrão conferidos.'))
