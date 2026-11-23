from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect

from .permissoes import codigos, tem_permissao


class PermissaoRequeridaMixin(LoginRequiredMixin):
    permissao = None

    def dispatch(self, request, *args, **kwargs):
        codigos(self.permissao)
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not tem_permissao(request.user, self.permissao):
            messages.error(request, 'Você não tem permissão para acessar esta página.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
