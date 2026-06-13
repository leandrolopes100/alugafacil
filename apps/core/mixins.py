from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect


class GrupoRequiredMixin(LoginRequiredMixin):
    """
    Exige que o usuario pertenca a um dos grupos listados em grupos_permitidos.
    Superusuarios sempre passam. Se grupos_permitidos estiver vazio, comporta-se
    como LoginRequiredMixin puro.
    """
    grupos_permitidos = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_superuser or not self.grupos_permitidos:
            return super().dispatch(request, *args, **kwargs)
        if not hasattr(request.user, '_grupos_nomes_cache'):
            request.user._grupos_nomes_cache = set(
                request.user.groups.values_list('name', flat=True)
            )
        if request.user._grupos_nomes_cache & set(self.grupos_permitidos):
            return super().dispatch(request, *args, **kwargs)
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('core:dashboard')
