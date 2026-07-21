from django.contrib import messages
from apps.core.mixins import GrupoRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from .forms import ClienteForm, CNHClienteForm
from .models import Cliente, CNHCliente


class ClienteListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'atendente']
    model = Cliente
    template_name = 'customers/lista.html'
    context_object_name = 'clientes'
    paginate_by = 20

    def get_queryset(self):
        qs = Cliente.objects.all()
        busca = self.request.GET.get('busca')
        tipo = self.request.GET.get('tipo')
        situacao = self.request.GET.get('situacao')
        if busca:
            from django.db.models import Q
            qs = qs.filter(
                Q(nome__icontains=busca) |
                Q(cpf__icontains=busca) |
                Q(razao_social__icontains=busca) |
                Q(cnpj__icontains=busca) |
                Q(telefone__icontains=busca) |
                Q(celular__icontains=busca)
            )
        if tipo:
            qs = qs.filter(tipo=tipo)
        if situacao:
            qs = qs.filter(situacao=situacao)
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['filtro_busca'] = self.request.GET.get('busca', '')
        contexto['filtro_tipo'] = self.request.GET.get('tipo', '')
        contexto['filtro_situacao'] = self.request.GET.get('situacao', '')
        return contexto


class ClienteCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora', 'atendente']
    model = Cliente
    form_class = ClienteForm
    template_name = 'customers/form.html'

    def get_success_url(self):
        return reverse_lazy('clientes:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Novo Cliente'
        contexto['acao'] = 'Cadastrar'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, f'Cliente {form.instance.nome_exibicao} cadastrado com sucesso.')
        return super().form_valid(form)


class ClienteDetailView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'atendente']
    model = Cliente
    template_name = 'customers/detalhe.html'
    context_object_name = 'cliente'

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['cnhs'] = self.object.cnhs.all()
        contexto['contratos_recentes'] = self.object.contratos.select_related('veiculo').order_by('-criado_em')[:10]
        return contexto


class ClienteUpdateView(GrupoRequiredMixin, UpdateView):
    grupos_permitidos = ['admin_locadora', 'atendente']
    model = Cliente
    form_class = ClienteForm
    template_name = 'customers/form.html'

    def get_success_url(self):
        return reverse_lazy('clientes:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = f'Editar {self.object.nome_exibicao}'
        contexto['acao'] = 'Salvar'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, 'Cliente atualizado com sucesso.')
        return super().form_valid(form)


class ClienteCNHView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente']
    template_name = 'customers/cnh.html'

    def get(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        form = CNHClienteForm()
        return self._render(request, cliente, form)

    def post(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        form = CNHClienteForm(request.POST, request.FILES)
        if form.is_valid():
            cnh = form.save(commit=False)
            cnh.cliente = cliente
            cnh.save()
            messages.success(request, 'CNH adicionada com sucesso.')
            return redirect('clientes:detalhe', pk=pk)
        return self._render(request, cliente, form)

    def _render(self, request, cliente, form):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'cliente': cliente,
            'cnhs': cliente.cnhs.all(),
            'form': form,
        })


class ClienteCNHExcluirView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora']
    def post(self, request, pk, cnh_pk):
        cnh = get_object_or_404(CNHCliente, pk=cnh_pk, cliente_id=pk)
        cnh.delete()
        messages.success(request, 'CNH removida.')
        return redirect('clientes:detalhe', pk=pk)
