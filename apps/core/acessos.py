from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from .models import PerfilAcesso
from .permissoes import PERMISSAO_ADMINISTRAR_ACESSOS


class AcessoInvalido(Exception):
    pass


def administradores_ativos():
    via_grupo = Q(
        groups__permissions__codename=PERMISSAO_ADMINISTRAR_ACESSOS,
        groups__permissions__content_type__app_label='core',
    )
    via_usuario = Q(
        user_permissions__codename=PERMISSAO_ADMINISTRAR_ACESSOS,
        user_permissions__content_type__app_label='core',
    )
    return get_user_model().objects.filter(is_active=True).filter(
        Q(is_superuser=True) | via_grupo | via_usuario
    ).distinct()


def validar_administracao(usuario_atual):
    administradores = administradores_ativos()
    if not usuario_atual.is_superuser and not administradores.filter(pk=usuario_atual.pk).exists():
        raise AcessoInvalido(
            'Você não pode retirar o seu próprio acesso a "Usuários e perfis de acesso". Nada foi alterado.'
        )
    if not administradores.exists():
        raise AcessoInvalido(
            'O sistema precisa de pelo menos um usuário ativo com acesso a "Usuários e perfis de acesso". '
            'Nada foi alterado.'
        )


def alterar_acessos(usuario_atual, alteracao):
    with transaction.atomic():
        list(PerfilAcesso.objects.select_for_update().order_by('pk').values_list('pk', flat=True))
        resultado = alteracao()
        validar_administracao(usuario_atual)
    return resultado
