from django.contrib import messages
from django.db import transaction
from apps.core.mixins import PermissaoRequeridaMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import AlertaManutencaoForm, OrdemManutencaoForm
from .models import AlertaManutencao, OrdemManutencao


def _avisar_reserva(request, veiculo):
    from apps.contracts.models import Reserva
    reserva = Reserva.objects.filter(veiculo=veiculo, situacao='confirmada').first()
    if reserva:
        messages.warning(
            request,
            f'Atenção: o veículo {veiculo.placa} possui a reserva #{reserva.pk} confirmada para '
            f'{reserva.data_retirada.strftime("%d/%m/%Y")}. Verifique e cancele a reserva se necessário.'
        )


def _salvar_ordem(request, ordem, situacao_anterior=None, veiculo_anterior_id=None):
    hoje = timezone.localdate()
    if ordem.situacao == 'em_andamento' and not ordem.data_entrada:
        ordem.data_entrada = hoje
    if ordem.situacao == 'concluida' and not ordem.data_saida:
        ordem.data_saida = hoje
    with transaction.atomic():
        ordem.save()
        if ordem.situacao == 'concluida' and situacao_anterior != 'concluida':
            ordem.aplicar_conclusao()
        OrdemManutencao.sincronizar_situacao_veiculos(ordem.veiculo_id, veiculo_anterior_id)
    if ordem.situacao == 'em_andamento' and situacao_anterior != 'em_andamento':
        _avisar_reserva(request, ordem.veiculo)
    if ordem.situacao == 'concluida' and situacao_anterior != 'concluida' and ordem.custo_total:
        messages.info(
            request,
            f'Despesa de R$ {ordem.custo_total} lançada automaticamente em '
            f'Financeiro → Despesas Operacionais.'
        )


class ManutencaoListView(PermissaoRequeridaMixin, ListView):
    permissao = 'manutencao_acessar'
    model = OrdemManutencao
    template_name = 'manutencao/lista.html'
    context_object_name = 'ordens'
    paginate_by = 20

    def get_queryset(self):
        qs = OrdemManutencao.objects.select_related('veiculo')
        situacao = self.request.GET.get('situacao')
        tipo = self.request.GET.get('tipo')
        if situacao:
            qs = qs.filter(situacao=situacao)
        if tipo:
            qs = qs.filter(tipo=tipo)
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['situacoes'] = OrdemManutencao.SITUACAO
        contexto['tipos'] = OrdemManutencao.TIPO
        contexto['filtro_situacao'] = self.request.GET.get('situacao', '')
        return contexto


class ManutencaoCreateView(PermissaoRequeridaMixin, CreateView):
    permissao = 'manutencao_acessar'
    model = OrdemManutencao
    form_class = OrdemManutencaoForm
    template_name = 'manutencao/form.html'

    def get_success_url(self):
        return reverse('manutencao:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Nova Ordem de Manutenção'
        return contexto

    def form_valid(self, form):
        os_obj = form.save(commit=False)
        _salvar_ordem(self.request, os_obj)
        self.object = os_obj
        messages.success(self.request, f'OS criada para {os_obj.veiculo.placa}.')
        return redirect(self.get_success_url())


class ManutencaoDetailView(PermissaoRequeridaMixin, DetailView):
    permissao = 'manutencao_acessar'
    model = OrdemManutencao
    template_name = 'manutencao/detalhe.html'
    context_object_name = 'ordem'


class ManutencaoUpdateView(PermissaoRequeridaMixin, UpdateView):
    permissao = 'manutencao_acessar'
    model = OrdemManutencao
    form_class = OrdemManutencaoForm
    template_name = 'manutencao/form.html'

    def get_success_url(self):
        return reverse('manutencao:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = f'Editar OS #{self.object.pk} — {self.object.veiculo.placa}'
        return contexto

    def form_valid(self, form):
        anterior = OrdemManutencao.objects.values('situacao', 'veiculo_id').get(pk=self.object.pk)
        os_obj = form.save(commit=False)
        _salvar_ordem(self.request, os_obj, anterior['situacao'], anterior['veiculo_id'])
        messages.success(self.request, 'OS atualizada.')
        return redirect(self.get_success_url())


class ManutencaoAlterarStatusView(PermissaoRequeridaMixin, View):
    permissao = 'manutencao_acessar'

    _TRANSICOES = {
        'agendada':     ['em_andamento', 'cancelada'],
        'em_andamento': ['concluida', 'cancelada'],
        'concluida':    [],
        'cancelada':    [],
    }

    def post(self, request, pk):
        ordem = get_object_or_404(OrdemManutencao, pk=pk)
        novo_status = request.POST.get('situacao', '')

        if novo_status not in self._TRANSICOES.get(ordem.situacao, []):
            messages.error(request, 'Transição de status inválida.')
            return redirect(reverse('manutencao:detalhe', kwargs={'pk': pk}))

        situacao_anterior = ordem.situacao
        ordem.situacao = novo_status
        erro = ordem.erro_ao_iniciar()
        if erro:
            messages.error(request, erro)
            return redirect(reverse('manutencao:detalhe', kwargs={'pk': pk}))
        _salvar_ordem(request, ordem, situacao_anterior, ordem.veiculo_id)
        labels = dict(OrdemManutencao.SITUACAO)
        messages.success(request, f'Status alterado para "{labels[novo_status]}".')
        return redirect(reverse('manutencao:detalhe', kwargs={'pk': pk}))


class AlertaListView(PermissaoRequeridaMixin, ListView):
    permissao = 'manutencao_acessar'
    model = AlertaManutencao
    template_name = 'manutencao/alertas.html'
    context_object_name = 'alertas'

    def get_queryset(self):
        return AlertaManutencao.objects.filter(ativo=True).select_related('veiculo__grupo')

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        alertas = list(self.object_list)
        contexto['alertas_vencidos'] = [a for a in alertas if a.vencido]
        contexto['alertas_proximos'] = [a for a in alertas if a.proximo]
        contexto['alertas_ok'] = [a for a in alertas if not a.vencido and not a.proximo]
        return contexto


class AlertaCreateView(PermissaoRequeridaMixin, CreateView):
    permissao = 'manutencao_acessar'
    model = AlertaManutencao
    form_class = AlertaManutencaoForm
    template_name = 'manutencao/alerta_form.html'
    success_url = reverse_lazy('manutencao:alertas')

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['titulo'] = 'Novo Alerta de Manutenção'
        return contexto

    def form_valid(self, form):
        messages.success(self.request, 'Alerta criado.')
        return super().form_valid(form)
