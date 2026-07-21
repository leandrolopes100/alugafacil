from django.contrib import messages
from apps.core.mixins import GrupoRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView, View
)

from .forms import VeiculoForm, GrupoVeiculoForm, FotoVeiculoForm, DocumentoVeiculoForm, CategoriaVeiculoForm
from .models import CategoriaVeiculo, DocumentoVeiculo, FotoVeiculo, GrupoVeiculo, Veiculo


class VeiculoListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    model = Veiculo
    template_name = 'fleet/lista.html'
    context_object_name = 'veiculos'
    paginate_by = 20

    def get_queryset(self):
        qs = Veiculo.objects.select_related('grupo__categoria')
        situacao = self.request.GET.get('situacao')
        busca = self.request.GET.get('busca')
        grupo = self.request.GET.get('grupo')
        if situacao:
            qs = qs.filter(situacao=situacao)
        if busca:
            from django.db.models import Q
            qs = qs.filter(
                Q(placa__icontains=busca) |
                Q(marca__icontains=busca) |
                Q(modelo__icontains=busca)
            )
        if grupo:
            qs = qs.filter(grupo_id=grupo)
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['grupos'] = GrupoVeiculo.objects.filter(ativo=True)
        contexto['filtro_situacao'] = self.request.GET.get('situacao', '')
        contexto['filtro_busca'] = self.request.GET.get('busca', '')
        # Contagens por situação em 1 query
        from django.db.models import Count
        sit_map = {
            r['situacao']: r['n']
            for r in Veiculo.objects.values('situacao').annotate(n=Count('id'))
        }
        contexto['situacoes_contagem'] = [
            (valor, rotulo, sit_map.get(valor, 0))
            for valor, rotulo in Veiculo.SITUACAO
        ]
        return contexto


class VeiculoCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    model = Veiculo
    form_class = VeiculoForm
    template_name = 'fleet/form.html'

    def get_success_url(self):
        return reverse_lazy('frota:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Cadastrar Veículo'
        contexto['acao'] = 'Cadastrar'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, f'Veículo {form.instance.placa} cadastrado com sucesso.')
        return super().form_valid(form)


class VeiculoDetailView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    model = Veiculo
    template_name = 'fleet/detalhe.html'
    context_object_name = 'veiculo'

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['fotos'] = self.object.fotos.all()
        contexto['documentos'] = self.object.documentos.all()
        contexto['contratos_recentes'] = self.object.contratos.select_related('cliente').order_by('-criado_em')[:5]
        contexto['historico_km'] = self.object.historico_km.select_related(
            'contrato', 'registrado_por'
        ).order_by('-data')[:15]
        contexto['pode_excluir'] = (
            self.object.situacao in ('disponivel', 'inativo') and
            not self.object.contratos.exists()
        )
        return contexto


class VeiculoDeleteView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora']
    def post(self, request, pk):
        from django.db import ProtectedError
        veiculo = get_object_or_404(Veiculo, pk=pk, situacao__in=['disponivel', 'inativo'])
        placa = veiculo.placa
        representacao = str(veiculo)
        try:
            veiculo.delete()
            messages.success(request, f'Veículo {representacao} excluído com sucesso.')
            return redirect('frota:lista')
        except ProtectedError:
            messages.error(request, f'Não é possível excluir o veículo {placa}: existem contratos vinculados a ele.')
            return redirect('frota:detalhe', pk=pk)


class VeiculoUpdateView(GrupoRequiredMixin, UpdateView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    model = Veiculo
    form_class = VeiculoForm
    template_name = 'fleet/form.html'

    def get_success_url(self):
        return reverse_lazy('frota:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = f'Editar {self.object.placa}'
        contexto['acao'] = 'Salvar'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, 'Veículo atualizado com sucesso.')
        return super().form_valid(form)


class VeiculoFotosView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    template_name = 'fleet/fotos.html'

    def get(self, request, pk):
        veiculo = get_object_or_404(Veiculo, pk=pk)
        form = FotoVeiculoForm()
        return self._render(request, veiculo, form)

    def post(self, request, pk):
        veiculo = get_object_or_404(Veiculo, pk=pk)
        form = FotoVeiculoForm(request.POST, request.FILES)
        if form.is_valid():
            foto = form.save(commit=False)
            foto.veiculo = veiculo
            foto.save()
            if request.htmx:
                fotos = veiculo.fotos.all()
                from django.template.loader import render_to_string
                html = render_to_string('fleet/partials/lista_fotos.html', {'veiculo': veiculo, 'fotos': fotos}, request=request)
                return JsonResponse({'html': html})
            messages.success(request, 'Foto adicionada.')
            return redirect('frota:fotos', pk=pk)
        return self._render(request, veiculo, form)

    def _render(self, request, veiculo, form):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'veiculo': veiculo,
            'fotos': veiculo.fotos.all(),
            'form': form,
        })


class VeiculoFotoExcluirView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora']
    def post(self, request, pk, foto_pk):
        foto = get_object_or_404(FotoVeiculo, pk=foto_pk, veiculo_id=pk)
        foto.delete()
        if request.htmx:
            return JsonResponse({'ok': True})
        messages.success(request, 'Foto removida.')
        return redirect('frota:fotos', pk=pk)


class VeiculoDocumentosView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    template_name = 'fleet/documentos.html'

    def get(self, request, pk):
        veiculo = get_object_or_404(Veiculo, pk=pk)
        form = DocumentoVeiculoForm()
        return self._render(request, veiculo, form)

    def post(self, request, pk):
        veiculo = get_object_or_404(Veiculo, pk=pk)
        form = DocumentoVeiculoForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.veiculo = veiculo
            doc.save()
            messages.success(request, 'Documento adicionado.')
            return redirect('frota:documentos', pk=pk)
        return self._render(request, veiculo, form)

    def _render(self, request, veiculo, form):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'veiculo': veiculo,
            'documentos': veiculo.documentos.all(),
            'form': form,
        })


class VeiculoDocumentoExcluirView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora']
    def post(self, request, pk, doc_pk):
        doc = get_object_or_404(DocumentoVeiculo, pk=doc_pk, veiculo_id=pk)
        doc.delete()
        messages.success(request, 'Documento removido.')
        return redirect('frota:documentos', pk=pk)


class GrupoListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    model = GrupoVeiculo
    template_name = 'fleet/grupos.html'
    context_object_name = 'grupos'

    def get_queryset(self):
        from django.db.models import Count
        return GrupoVeiculo.objects.select_related('categoria').annotate(
            total_veiculos=Count('veiculos')
        )

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['categorias'] = CategoriaVeiculo.objects.all().order_by('ordem', 'nome')
        contexto['sem_categorias'] = not contexto['categorias'].exists()
        return contexto


class GrupoCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora']
    model = GrupoVeiculo
    form_class = GrupoVeiculoForm
    template_name = 'fleet/grupo_form.html'
    success_url = reverse_lazy('frota:grupos')

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Novo Grupo de Veículo'
        contexto['sem_categorias'] = not CategoriaVeiculo.objects.exists()
        return contexto

    def form_valid(self, form):
        messages.success(self.request, f'Grupo "{form.instance.nome}" criado com sucesso.')
        return super().form_valid(form)


class GrupoUpdateView(GrupoRequiredMixin, UpdateView):
    grupos_permitidos = ['admin_locadora']
    model = GrupoVeiculo
    form_class = GrupoVeiculoForm
    template_name = 'fleet/grupo_form.html'
    success_url = reverse_lazy('frota:grupos')

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = f'Editar — {self.object.nome}'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, f'Grupo "{form.instance.nome}" atualizado.')
        return super().form_valid(form)


# ─── Categorias ──────────────────────────────────────────────────────────────

class CategoriaListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    model = CategoriaVeiculo
    template_name = 'fleet/categorias.html'
    context_object_name = 'categorias'

    def get_queryset(self):
        from django.db.models import Count
        return CategoriaVeiculo.objects.annotate(
            total_grupos=Count('grupos')
        ).order_by('ordem', 'nome')


_SUGESTOES_CATEGORIA = [
    {'nome': 'Econômico',   'icone': 'bi-car-front',        'cor': '#3B82F6'},
    {'nome': 'Executivo',   'icone': 'bi-car-front-fill',   'cor': '#8B5CF6'},
    {'nome': 'SUV',         'icone': 'bi-truck',            'cor': '#10B981'},
    {'nome': 'Utilitário',  'icone': 'bi-box-seam',         'cor': '#F59E0B'},
    {'nome': 'Van',         'icone': 'bi-bus-front',        'cor': '#EF4444'},
    {'nome': 'Moto',        'icone': 'bi-bicycle',          'cor': '#6B7280'},
    {'nome': 'Elétrico',    'icone': 'bi-lightning-charge', 'cor': '#06B6D4'},
    {'nome': 'Pickup',      'icone': 'bi-truck-flatbed',    'cor': '#D97706'},
]


class CategoriaCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora']
    model = CategoriaVeiculo
    form_class = CategoriaVeiculoForm
    template_name = 'fleet/categoria_form.html'

    def get_success_url(self):
        # Se veio de outro formulário (ex: novo grupo), volta para lá
        prox = self.request.POST.get('next') or self.request.GET.get('next')
        if prox:
            return prox
        return reverse_lazy('frota:categorias')

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Nova Categoria'
        contexto['sugestoes'] = _SUGESTOES_CATEGORIA
        contexto['next'] = self.request.GET.get('next', '')
        return contexto

    def form_valid(self, form):
        messages.success(self.request, f'Categoria "{form.instance.nome}" criada com sucesso.')
        return super().form_valid(form)


class CategoriaUpdateView(GrupoRequiredMixin, UpdateView):
    grupos_permitidos = ['admin_locadora']
    model = CategoriaVeiculo
    form_class = CategoriaVeiculoForm
    template_name = 'fleet/categoria_form.html'
    success_url = reverse_lazy('frota:categorias')

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = f'Editar — {self.object.nome}'
        contexto['sugestoes'] = _SUGESTOES_CATEGORIA
        return contexto

    def form_valid(self, form):
        messages.success(self.request, f'Categoria "{form.instance.nome}" atualizada.')
        return super().form_valid(form)


class DisponibilidadeView(GrupoRequiredMixin, TemplateView):
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']
    template_name = 'fleet/disponibilidade.html'

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        grupos = GrupoVeiculo.objects.filter(ativo=True).select_related('categoria').prefetch_related('veiculos')
        totais = {}
        grupos_data = []
        for grupo in grupos:
            veiculos = list(grupo.veiculos.all())
            if not veiculos:
                continue
            contagens = {}
            for v in veiculos:
                contagens[v.situacao] = contagens.get(v.situacao, 0) + 1
                totais[v.situacao] = totais.get(v.situacao, 0) + 1
            grupos_data.append({
                'grupo': grupo,
                'veiculos': veiculos,
                'contagens': contagens,
                'total': len(veiculos),
            })
        contexto['grupos_data'] = grupos_data
        contexto['totais'] = totais
        return contexto


class VeiculoTarifasView(GrupoRequiredMixin, View):
    """Retorna tarifas do grupo de um veículo — usado via HTMX no form de contrato."""
    grupos_permitidos = ['admin_locadora', 'atendente', 'mecanico']

    def get(self, request):
        veiculo_id = request.GET.get('veiculo_id')
        if not veiculo_id:
            return JsonResponse({})
        try:
            veiculo = Veiculo.objects.select_related('grupo').get(pk=veiculo_id)
            grupo = veiculo.grupo
            return JsonResponse({
                'diaria': str(grupo.diaria),
                'km_franquia_diaria': grupo.km_franquia_diaria,
                'valor_km_excedente': str(grupo.valor_km_excedente),
                'caucao': str(grupo.caucao),
                'grupo_nome': grupo.nome,
            })
        except Veiculo.DoesNotExist:
            return JsonResponse({})
