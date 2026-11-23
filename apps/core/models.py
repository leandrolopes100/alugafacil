from django.contrib.auth.models import Group, Permission
from django.db import models
from django.db.models.functions import Lower
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.text import slugify

from .permissoes import PERMISSOES


class PerfilAcesso(models.Model):
    grupo = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='perfil')
    nome = models.CharField('nome', max_length=80)
    descricao = models.CharField('descrição', max_length=200, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'perfil de acesso'
        verbose_name_plural = 'perfis de acesso'
        ordering = ['nome']
        default_permissions = ()
        permissions = list(PERMISSOES.items())
        constraints = [
            models.UniqueConstraint(Lower('nome'), name='perfil_acesso_nome_unico'),
        ]

    def __str__(self):
        return self.nome

    @classmethod
    def nome_livre(cls, nome, exceto=None):
        candidato, n = nome[:80], 2
        outros = cls.objects.exclude(pk=exceto) if exceto else cls.objects.all()
        while outros.filter(nome__iexact=candidato).exists():
            sufixo = f' ({n})'
            candidato, n = nome[:80 - len(sufixo)] + sufixo, n + 1
        return candidato

    @classmethod
    def criar(cls, nome, descricao=''):
        base = f'perfil-{slugify(nome)[:120] or "sem-nome"}'
        interno, n = base, 2
        while Group.objects.filter(name=interno).exists():
            interno, n = f'{base}-{n}', n + 1
        grupo = Group.objects.create(name=interno)
        perfil = grupo.perfil
        perfil.nome = nome
        perfil.descricao = descricao
        perfil.save()
        return perfil

    @staticmethod
    def permissoes_do_sistema():
        return Permission.objects.filter(
            content_type__app_label='core', content_type__model='perfilacesso', codename__in=PERMISSOES,
        )

    @property
    def codigos(self):
        return set(
            self.grupo.permissions.filter(
                content_type__app_label='core', content_type__model='perfilacesso',
            ).values_list('codename', flat=True)
        )

    def definir_permissoes(self, codigos):
        do_sistema = list(self.permissoes_do_sistema())
        self.grupo.permissions.remove(*do_sistema)
        self.grupo.permissions.add(*[p for p in do_sistema if p.codename in set(codigos)])


@receiver(post_save, sender=Group)
def criar_perfil_do_grupo(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        PerfilAcesso.objects.get_or_create(
            grupo=instance, defaults={'nome': PerfilAcesso.nome_livre(instance.name)},
        )
