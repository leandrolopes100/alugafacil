from django.contrib import messages
from apps.core.mixins import GrupoRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.fleet.models import HistoricoKmVeiculo
from .forms import AlertaManutencaoForm, OrdemManutencaoForm
from .models import AlertaManutencao, OrdemManutencao


def _registrar_km_manutencao(veiculo, ordem):
    """Cria HistoricoKmVeiculo para a OS se ainda nao existir registro para ela."""
    marcador = f'OS #{ordem.pk}'
    if not HistoricoKmVeiculo.objects.filter(
        veiculo=veiculo,
        origem='manutencao',
        observacao__startswith=marcador,
    ).exists():
        HistoricoKmVeiculo.objects.create(
            veiculo=veiculo,
            origem='manutencao',
            km=ordem.km_na_manutencao,
            data=timezone.now(),
            observacao=f'{marcador} — {ordem.descricao[:100]}',
        )


class ManutencaoListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'mecanico']
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


class ManutencaoCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora', 'mecanico']
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
        if os_obj.situacao in ('agendada', 'em_andamento'):
            from apps.contracts.models import Reserva
            reserva_ativa = Reserva.objects.filter(
                veiculo=os_obj.veiculo,
                situacao='confirmada',
            ).first()
            if reserva_ativa:
                messages.warning(
                    self.request,
                    f'Atenção: o veículo {os_obj.veiculo.placa} possui a reserva '
                    f'#{reserva_ativa.pk} confirmada para '
                    f'{reserva_ativa.data_retirada.strftime("%d/%m/%Y")}. '
                    f'Verifique e cancele a reserva se necessário.'
                )
            os_obj.veiculo.situacao = 'manutencao'
            os_obj.veiculo.save()
        os_obj.save()
        messages.success(self.request, f'OS criada para {os_obj.veiculo.placa}.')
        return redirect(self.get_success_url())


class ManutencaoDetailView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'mecanico']
    model = OrdemManutencao
    template_name = 'manutencao/detalhe.html'
    context_object_name = 'ordem'


class ManutencaoUpdateView(GrupoRequiredMixin, UpdateView):
    grupos_permitidos = ['admin_locadora', 'mecanico']
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
        os_obj = form.save(commit=False)
        if os_obj.situacao == 'concluida' and os_obj.veiculo.situacao == 'manutencao':
            os_obj.veiculo.situacao = 'disponivel'
            if os_obj.km_na_manutencao:
                os_obj.veiculo.km_atual = os_obj.km_na_manutencao
            os_obj.veiculo.save()
            if os_obj.km_na_manutencao:
                for alerta in os_obj.veiculo.alertas_manutencao.filter(tipo_alerta='km', ativo=True):
                    if alerta.vencido and alerta.km_intervalo:
                        alerta.km_proximo_servico = os_obj.km_na_manutencao + alerta.km_intervalo
                        alerta.save()
        os_obj.save()
        if os_obj.situacao == 'concluida' and os_obj.km_na_manutencao:
            _registrar_km_manutencao(os_obj.veiculo, os_obj)
        if os_obj.situacao == 'concluida' and os_obj.custo_total:
            messages.info(
                self.request,
                f'Despesa de R$ {os_obj.custo_total} lançada automaticamente em '
                f'Financeiro → Despesas Operacionais.'
            )
        messages.success(self.request, 'OS atualizada.')
        return redirect(self.get_success_url())


class ManutencaoAlterarStatusView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'mecanico']

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

        veiculo = ordem.veiculo
        ordem.situacao = novo_status

        if novo_status == 'em_andamento':
            if not ordem.data_entrada:
                ordem.data_entrada = timezone.now().date()
            if veiculo.situacao != 'manutencao':
                from apps.contracts.models import Reserva
                reserva_ativa = Reserva.objects.filter(
                    veiculo=veiculo,
                    situacao='confirmada',
                ).first()
                if reserva_ativa:
                    messages.warning(
                        request,
                        f'Atenção: o veículo {veiculo.placa} possui a reserva '
                        f'#{reserva_ativa.pk} confirmada para '
                        f'{reserva_ativa.data_retirada.strftime("%d/%m/%Y")}. '
                        f'Verifique e cancele a reserva se necessário.'
                    )
                veiculo.situacao = 'manutencao'
                veiculo.save()

        elif novo_status == 'concluida':
            if not ordem.data_saida:
                ordem.data_saida = timezone.now().date()
            if veiculo.situacao == 'manutencao':
                veiculo.situacao = 'disponivel'
                if ordem.km_na_manutencao:
                    veiculo.km_atual = ordem.km_na_manutencao
                veiculo.save()
            if ordem.km_na_manutencao:
                for alerta in veiculo.alertas_manutencao.filter(tipo_alerta='km', ativo=True):
                    if alerta.vencido and alerta.km_intervalo:
                        alerta.km_proximo_servico = ordem.km_na_manutencao + alerta.km_intervalo
                        alerta.save()
            if ordem.km_na_manutencao:
                _registrar_km_manutencao(veiculo, ordem)
            if ordem.custo_total:
                messages.info(
                    request,
                    f'Despesa de R$ {ordem.custo_total} lançada automaticamente em '
                    f'Financeiro → Despesas Operacionais.'
                )

        elif novo_status == 'cancelada':
            if veiculo.situacao == 'manutencao':
                veiculo.situacao = 'disponivel'
                veiculo.save()

        ordem.save()
        labels = dict(OrdemManutencao.SITUACAO)
        messages.success(request, f'Status alterado para "{labels[novo_status]}".')
        return redirect(reverse('manutencao:detalhe', kwargs={'pk': pk}))


class AlertaListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'mecanico']
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


class AlertaCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora', 'mecanico']
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
