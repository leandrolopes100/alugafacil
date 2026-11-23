from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .acessos import AcessoInvalido, alterar_acessos
from .forms import PerfilAcessoForm, RedefinirSenhaForm, UsuarioForm
from .mixins import PermissaoRequeridaMixin
from .models import PerfilAcesso
from .permissoes import PERMISSOES

PERMISSAO = 'admin_usuarios'


class PerfilListView(PermissaoRequeridaMixin, View):
    permissao = PERMISSAO

    def get(self, request):
        perfis = PerfilAcesso.objects.annotate(
            usuarios_ativos=Count('grupo__user', filter=Q(grupo__user__is_active=True), distinct=True),
            usuarios_inativos=Count('grupo__user', filter=Q(grupo__user__is_active=False), distinct=True),
            usuarios_total=Count('grupo__user', distinct=True),
            total_permissoes=Count(
                'grupo__permissions',
                filter=Q(grupo__permissions__content_type__app_label='core',
                         grupo__permissions__codename__in=list(PERMISSOES)),
                distinct=True,
            ),
        ).order_by('nome')
        return render(request, 'core/acessos/perfis.html', {
            'perfis': perfis, 'total_do_sistema': len(PERMISSOES),
        })


class _PerfilFormView(PermissaoRequeridaMixin, View):
    permissao = PERMISSAO
    template_name = 'core/acessos/perfil_form.html'

    def contexto(self, form, perfil=None):
        usuarios = list(perfil.grupo.user_set.order_by('username')) if perfil else []
        return {'form': form, 'perfil': perfil, 'usuarios_do_perfil': usuarios}

    def salvar(self, request, form, perfil=None):
        def aplicar():
            if perfil is None:
                novo = PerfilAcesso.criar(form.cleaned_data['nome'], form.cleaned_data['descricao'])
            else:
                novo = form.save()
            novo.definir_permissoes(form.cleaned_data['permissoes'])
            return novo

        try:
            salvo = alterar_acessos(request.user, aplicar)
        except AcessoInvalido as erro:
            form.add_error(None, str(erro))
            return render(request, self.template_name, self.contexto(form, perfil))
        messages.success(request, f'Perfil "{salvo.nome}" salvo.')
        return redirect('core:perfis')


class PerfilCreateView(_PerfilFormView):

    def get(self, request):
        copiar = request.GET.get('copiar')
        origem = PerfilAcesso.objects.filter(pk=copiar).first() if copiar and copiar.isdigit() else None
        return render(request, self.template_name, self.contexto(PerfilAcessoForm(copiar_de=origem)))

    def post(self, request):
        form = PerfilAcessoForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self.contexto(form))
        return self.salvar(request, form)


class PerfilUpdateView(_PerfilFormView):

    def get(self, request, pk):
        perfil = get_object_or_404(PerfilAcesso.objects.select_related('grupo'), pk=pk)
        return render(request, self.template_name, self.contexto(PerfilAcessoForm(instance=perfil), perfil))

    def post(self, request, pk):
        perfil = get_object_or_404(PerfilAcesso.objects.select_related('grupo'), pk=pk)
        form = PerfilAcessoForm(request.POST, instance=perfil)
        if not form.is_valid():
            return render(request, self.template_name, self.contexto(form, perfil))
        return self.salvar(request, form, perfil)


class PerfilDeleteView(PermissaoRequeridaMixin, View):
    permissao = PERMISSAO

    def post(self, request, pk):
        perfil = get_object_or_404(PerfilAcesso.objects.select_related('grupo'), pk=pk)

        def aplicar():
            if perfil.grupo.user_set.exists():
                raise AcessoInvalido(
                    f'O perfil "{perfil.nome}" tem usuários (ativos ou desativados). '
                    'Mude o perfil deles antes de excluir.'
                )
            perfil.grupo.delete()

        try:
            alterar_acessos(request.user, aplicar)
        except AcessoInvalido as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, f'Perfil "{perfil.nome}" excluído.')
        return redirect('core:perfis')


class UsuarioListView(PermissaoRequeridaMixin, View):
    permissao = PERMISSAO

    def get(self, request):
        from django.contrib.auth.models import Group
        usuarios = get_user_model().objects.prefetch_related(
            Prefetch('groups', queryset=Group.objects.select_related('perfil'))
        ).order_by('-is_active', 'username')
        return render(request, 'core/acessos/usuarios.html', {'usuarios': usuarios})


class _UsuarioAlvoMixin:

    def alvo(self, request, pk):
        usuario = get_object_or_404(get_user_model(), pk=pk)
        if usuario.is_superuser and not request.user.is_superuser:
            messages.error(request, 'Somente um superusuário pode alterar outro superusuário.')
            return None
        return usuario


class _UsuarioFormView(PermissaoRequeridaMixin, View):
    permissao = PERMISSAO
    template_name = 'core/acessos/usuario_form.html'

    def salvar(self, request, form, usuario=None):
        try:
            salvo = alterar_acessos(request.user, form.salvar)
        except AcessoInvalido as erro:
            form.add_error(None, str(erro))
            return render(request, self.template_name, {'form': form, 'usuario_alvo': usuario})
        messages.success(request, f'Usuário "{salvo.username}" salvo.')
        return redirect('core:usuarios')


class UsuarioCreateView(_UsuarioFormView):

    def get(self, request):
        return render(request, self.template_name, {'form': UsuarioForm(editor=request.user)})

    def post(self, request):
        form = UsuarioForm(request.POST, editor=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})
        return self.salvar(request, form)


class UsuarioUpdateView(_UsuarioAlvoMixin, _UsuarioFormView):

    def get(self, request, pk):
        usuario = self.alvo(request, pk)
        if usuario is None:
            return redirect('core:usuarios')
        form = UsuarioForm(instance=usuario, editor=request.user)
        return render(request, self.template_name, {'form': form, 'usuario_alvo': usuario})

    def post(self, request, pk):
        usuario = self.alvo(request, pk)
        if usuario is None:
            return redirect('core:usuarios')
        form = UsuarioForm(request.POST, instance=usuario, editor=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form, 'usuario_alvo': usuario})
        return self.salvar(request, form, usuario)


class UsuarioSenhaView(_UsuarioAlvoMixin, PermissaoRequeridaMixin, View):
    permissao = PERMISSAO
    template_name = 'core/acessos/usuario_senha.html'

    def get(self, request, pk):
        usuario = self.alvo(request, pk)
        if usuario is None:
            return redirect('core:usuarios')
        return render(request, self.template_name, {'form': RedefinirSenhaForm(usuario), 'usuario_alvo': usuario})

    def post(self, request, pk):
        usuario = self.alvo(request, pk)
        if usuario is None:
            return redirect('core:usuarios')
        form = RedefinirSenhaForm(usuario, request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form, 'usuario_alvo': usuario})
        form.save()
        if usuario.pk == request.user.pk:
            update_session_auth_hash(request, usuario)
        messages.success(request, f'Senha de "{usuario.username}" redefinida. As sessões abertas dele foram encerradas.'
                         if usuario.pk != request.user.pk else 'Sua senha foi alterada.')
        return redirect('core:usuarios')
