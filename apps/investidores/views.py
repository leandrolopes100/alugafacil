import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from apps.core.mixins import GrupoRequiredMixin
from .forms import (
    GerarCobrancaForm,
    GerarCobrancaLoteForm,
    InvestidorForm,
    PagarCobrancaForm,
    VincularVeiculoForm,
)
from .models import CobrancaGestao, Investidor, VeiculoInvestidor


def _semana_anterior():
    hoje = timezone.now().date()
    inicio = hoje - timedelta(days=hoje.weekday() + 7)
    fim = inicio + timedelta(days=6)
    return inicio, fim


def _data_vencimento_para(vinculo, semana_fim):
    dia = vinculo.dia_vencimento
    mes = semana_fim.month
    ano = semana_fim.year
    if mes == 12:
        mes_v, ano_v = 1, ano + 1
    else:
        mes_v, ano_v = mes + 1, ano
    ultimo = calendar.monthrange(ano_v, mes_v)[1]
    return date(ano_v, mes_v, min(dia, ultimo))


# ─── Investidores ─────────────────────────────────────────────────────────────

class InvestidorListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = Investidor
    template_name = 'investidores/lista.html'
    context_object_name = 'investidores'
    paginate_by = 20

    def get_queryset(self):
        qs = Investidor.objects.annotate(
            total_veiculos_ativos=Count('veiculos', filter=Q(veiculos__ativo=True)),
            total_pendente=Sum(
                'veiculos__cobrancas__valor',
                filter=Q(veiculos__cobrancas__situacao='pendente'),
            ),
        ).order_by('nome')
        busca = self.request.GET.get('busca', '').strip()
        if busca:
            qs = qs.filter(
                Q(nome__icontains=busca)
                | Q(razao_social__icontains=busca)
                | Q(cpf__icontains=busca)
                | Q(cnpj__icontains=busca)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['busca'] = self.request.GET.get('busca', '')
        ctx['total_geral_pendente'] = (
            CobrancaGestao.objects.filter(situacao='pendente')
            .aggregate(s=Sum('valor'))['s'] or Decimal('0.00')
        )
        ctx['total_veiculos_geridos'] = VeiculoInvestidor.objects.filter(ativo=True).count()
        return ctx


class InvestidorCreateView(GrupoRequiredMixin, CreateView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = Investidor
    form_class = InvestidorForm
    template_name = 'investidores/form.html'

    def get_success_url(self):
        return reverse_lazy('investidores:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = 'Novo Investidor'
        ctx['acao'] = 'Cadastrar'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f'Investidor {form.instance.nome_exibicao} cadastrado com sucesso.')
        return super().form_valid(form)


class InvestidorDetalheView(GrupoRequiredMixin, DetailView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = Investidor
    template_name = 'investidores/detalhe.html'
    context_object_name = 'investidor'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        investidor = self.object
        hoje = timezone.now()
        hoje_date = hoje.date()

        vinculos_ativos = list(
            investidor.veiculos.filter(ativo=True)
            .select_related('veiculo__grupo__categoria')
            .annotate(
                cobrancas_pendentes=Count('cobrancas', filter=Q(cobrancas__situacao='pendente')),
                total_pendente_v=Sum('cobrancas__valor', filter=Q(cobrancas__situacao='pendente')),
            )
        )
        ctx['vinculos_ativos'] = vinculos_ativos

        # Calcula quais vínculos não têm cobrança gerada para a semana anterior
        semana_anterior_inicio = hoje_date - timedelta(days=hoje_date.weekday() + 7)
        ids_com_cobranca_semana = set(
            CobrancaGestao.objects.filter(
                veiculo_investidor__investidor=investidor,
                veiculo_investidor__ativo=True,
                semana_inicio=semana_anterior_inicio,
            ).values_list('veiculo_investidor_id', flat=True)
        )
        ctx['vinculos_sem_cobranca_semana'] = {
            v.pk for v in vinculos_ativos
            if v.pk not in ids_com_cobranca_semana
            and v.data_inicio <= semana_anterior_inicio
        }

        ctx['vinculos_encerrados'] = (
            investidor.veiculos.filter(ativo=False)
            .select_related('veiculo')
            .order_by('-data_fim')[:5]
        )
        ctx['cobrancas_recentes'] = (
            CobrancaGestao.objects.filter(veiculo_investidor__investidor=investidor)
            .select_related('veiculo_investidor__veiculo')
            .order_by('-semana_inicio')[:12]
        )
        ctx['total_pendente'] = (
            CobrancaGestao.objects.filter(
                veiculo_investidor__investidor=investidor, situacao='pendente',
            ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')
        )
        ctx['total_pago_mes'] = (
            CobrancaGestao.objects.filter(
                veiculo_investidor__investidor=investidor,
                situacao='pago',
                data_pagamento__year=hoje.year,
                data_pagamento__month=hoje.month,
            ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')
        )
        return ctx


class InvestidorEditarView(GrupoRequiredMixin, UpdateView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = Investidor
    form_class = InvestidorForm
    template_name = 'investidores/form.html'

    def get_success_url(self):
        return reverse_lazy('investidores:detalhe', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = f'Editar — {self.object.nome_exibicao}'
        ctx['acao'] = 'Salvar Alterações'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Investidor atualizado com sucesso.')
        return super().form_valid(form)


# ─── Vínculos ─────────────────────────────────────────────────────────────────

class VincularVeiculoView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'investidores/vincular_veiculo.html'

    def _investidor(self, pk):
        return get_object_or_404(Investidor, pk=pk)

    def get(self, request, pk):
        investidor = self._investidor(pk)
        form = VincularVeiculoForm(initial={'data_inicio': timezone.now().date()})
        return render(request, self.template_name, {'investidor': investidor, 'form': form})

    def post(self, request, pk):
        investidor = self._investidor(pk)
        form = VincularVeiculoForm(request.POST)
        if form.is_valid():
            vinculo = form.save(commit=False)
            vinculo.investidor = investidor
            vinculo.save()
            messages.success(
                request,
                f'Veículo {vinculo.veiculo.placa} vinculado a {investidor.nome_exibicao}.',
            )
            return redirect('investidores:detalhe', pk=investidor.pk)
        return render(request, self.template_name, {'investidor': investidor, 'form': form})


class DesvincularVeiculoView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, vi_pk):
        vinculo = get_object_or_404(VeiculoInvestidor, pk=vi_pk, ativo=True)
        vinculo.ativo = False
        vinculo.data_fim = timezone.now().date()
        vinculo.save(update_fields=['ativo', 'data_fim'])
        messages.success(request, f'Vínculo do veículo {vinculo.veiculo.placa} encerrado.')
        return redirect('investidores:detalhe', pk=vinculo.investidor_id)


# ─── Cobranças ────────────────────────────────────────────────────────────────

class CobrancaListView(GrupoRequiredMixin, ListView):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    model = CobrancaGestao
    template_name = 'investidores/cobrancas.html'
    context_object_name = 'cobrancas'
    paginate_by = 30

    def get_queryset(self):
        qs = CobrancaGestao.objects.select_related(
            'veiculo_investidor__investidor',
            'veiculo_investidor__veiculo',
        )
        situacao = self.request.GET.get('situacao', '')
        investidor_id = self.request.GET.get('investidor', '')
        if situacao:
            qs = qs.filter(situacao=situacao)
        if investidor_id:
            qs = qs.filter(veiculo_investidor__investidor_id=investidor_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        hoje = timezone.now()
        ctx['situacao_filtro'] = self.request.GET.get('situacao', '')
        ctx['investidor_filtro'] = self.request.GET.get('investidor', '')
        ctx['investidores'] = Investidor.objects.filter(situacao='ativo').order_by('nome')
        ctx['situacoes'] = CobrancaGestao.SITUACAO
        ctx['total_pendente'] = (
            CobrancaGestao.objects.filter(situacao='pendente')
            .aggregate(s=Sum('valor'))['s'] or Decimal('0.00')
        )
        ctx['total_pago_mes'] = (
            CobrancaGestao.objects.filter(
                situacao='pago',
                data_pagamento__year=hoje.year,
                data_pagamento__month=hoje.month,
            ).aggregate(s=Sum('valor'))['s'] or Decimal('0.00')
        )
        return ctx


class GerarCobrancaView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'investidores/gerar_cobranca.html'

    def _vinculo(self, vi_pk):
        return get_object_or_404(VeiculoInvestidor, pk=vi_pk, ativo=True)

    def get(self, request, vi_pk):
        vinculo = self._vinculo(vi_pk)
        inicio, fim = _semana_anterior()
        form = GerarCobrancaForm(initial={
            'semana_inicio': inicio,
            'semana_fim': fim,
            'valor': vinculo.taxa_gestao_semanal,
            'data_vencimento': _data_vencimento_para(vinculo, fim),
        })
        return render(request, self.template_name, {'vinculo': vinculo, 'form': form})

    def post(self, request, vi_pk):
        vinculo = self._vinculo(vi_pk)
        form = GerarCobrancaForm(request.POST)
        if form.is_valid():
            cobranca = form.save(commit=False)
            cobranca.veiculo_investidor = vinculo
            cobranca.save()
            messages.success(request, f'Cobrança de R$ {cobranca.valor:.2f} gerada com sucesso.')
            return redirect('investidores:detalhe', pk=vinculo.investidor_id)
        return render(request, self.template_name, {'vinculo': vinculo, 'form': form})


class GerarCobrancaLoteView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'investidores/gerar_lote.html'

    def _vinculos_ativos(self):
        return VeiculoInvestidor.objects.filter(ativo=True).select_related('investidor', 'veiculo')

    def get(self, request):
        inicio, fim = _semana_anterior()
        form = GerarCobrancaLoteForm(initial={'semana_inicio': inicio, 'semana_fim': fim})
        vinculos = self._vinculos_ativos()
        return render(request, self.template_name, {
            'form': form,
            'vinculos': vinculos,
            'total': vinculos.count(),
        })

    def post(self, request):
        form = GerarCobrancaLoteForm(request.POST)
        if not form.is_valid():
            vinculos = self._vinculos_ativos()
            return render(request, self.template_name, {
                'form': form, 'vinculos': vinculos, 'total': vinculos.count(),
            })

        semana_inicio = form.cleaned_data['semana_inicio']
        semana_fim = form.cleaned_data['semana_fim']
        criadas = ignoradas = 0

        vinculos = list(self._vinculos_ativos())
        existentes = set(
            CobrancaGestao.objects.filter(
                veiculo_investidor__in=vinculos,
                semana_inicio=semana_inicio,
            ).values_list('veiculo_investidor_id', flat=True)
        )

        for vinculo in vinculos:
            if vinculo.pk in existentes:
                ignoradas += 1
                continue
            CobrancaGestao.objects.create(
                veiculo_investidor=vinculo,
                semana_inicio=semana_inicio,
                semana_fim=semana_fim,
                valor=vinculo.taxa_gestao_semanal,
                data_vencimento=_data_vencimento_para(vinculo, semana_fim),
            )
            criadas += 1

        if criadas:
            messages.success(request, f'{criadas} cobrança(s) gerada(s) com sucesso.')
        if ignoradas:
            messages.info(request, f'{ignoradas} vínculo(s) ignorado(s) — já possuíam cobrança para esta semana.')
        return redirect('investidores:cobrancas')


class PagarCobrancaView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']
    template_name = 'investidores/pagar_cobranca.html'

    def _cobranca(self, pk):
        return get_object_or_404(CobrancaGestao, pk=pk, situacao='pendente')

    def get(self, request, pk):
        cobranca = self._cobranca(pk)
        form = PagarCobrancaForm(initial={'data_pagamento': timezone.now().date()})
        return render(request, self.template_name, {'cobranca': cobranca, 'form': form})

    def post(self, request, pk):
        cobranca = self._cobranca(pk)
        form = PagarCobrancaForm(request.POST)
        if form.is_valid():
            cobranca.situacao = 'pago'
            cobranca.data_pagamento = form.cleaned_data['data_pagamento']
            cobranca.forma_pagamento = form.cleaned_data['forma_pagamento']
            cobranca.observacoes = form.cleaned_data.get('observacoes', '')
            cobranca.save(update_fields=['situacao', 'data_pagamento', 'forma_pagamento', 'observacoes'])
            messages.success(request, f'Cobrança de R$ {cobranca.valor:.2f} registrada como paga.')
            return redirect('investidores:cobrancas')
        return render(request, self.template_name, {'cobranca': cobranca, 'form': form})


class CancelarCobrancaView(GrupoRequiredMixin, View):
    grupos_permitidos = ['admin_locadora', 'financeiro']

    def post(self, request, pk):
        cobranca = get_object_or_404(CobrancaGestao, pk=pk, situacao='pendente')
        cobranca.situacao = 'cancelado'
        cobranca.save(update_fields=['situacao'])
        messages.warning(request, 'Cobrança cancelada.')
        return redirect('investidores:cobrancas')
