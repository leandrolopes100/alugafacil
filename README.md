# Aluga Fácil

Sistema web para gestão de locadoras de veículos: controle de frota, clientes, contratos, financeiro e manutenção em um único sistema.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Django 5.2 · Python 3.12 |
| Banco de dados | SQLite (desenvolvimento) · PostgreSQL (produção) |
| Frontend | Bootstrap 5 · Alpine.js · HTMX |
| Tarefas assíncronas | Celery 5.5 + Redis |
| Arquivos estáticos | Whitenoise |
| Storage em produção | django-storages + boto3 (S3) |
| PDF | xhtml2pdf |
| Variáveis de ambiente | python-decouple |

---

## Módulos

### Frota (`fleet`)
- Categorias e grupos de veículos com tarifas (diária, semanal, mensal, franquia km, caução)
- Situações do veículo: `disponível` · `reservado` · `em uso` · `manutenção` · `inativo`
- Fotos e documentos (CRLV, seguro, extintor) com alertas de vencimento
- Histórico de quilometragem por contrato

### Clientes (`customers`)
- Pessoas físicas e jurídicas
- CNH com categoria, validade e fotos frente/verso
- Situação: `ativo` · `bloqueado`
- Busca por nome, CPF/CNPJ ou celular

### Contratos (`contracts`)
- Fluxo completo: **Reserva → Contrato → Checkout → Checkin → Encerramento**
- Numeração automática `AF-YYYY-NNNN`
- Situações: `aberto` · `ativo` · `aguardando_devolucao` · `encerrado` · `cancelado`
- Checklist de checkout com bloqueadores (documentos vencidos, caução pendente)
- Parcelas semanais upfront com fechamento automático FIFO ao receber pagamento
- Adicionais (GPS, cadeirinha, condutor extra, seguros) e registro de avarias
- Assinatura digital por link (token UUID)
- Properties calculadas: `total_locacao` · `total_adicionais` · `total_avarias` · `total_geral` · `total_pago` · `saldo_devedor`

### Financeiro (`financeiro`)
- Contas a receber vinculadas automaticamente a contratos via signal
- Despesas operacionais com parcelamento e débito automático
- Multas de trânsito com auto-associação por período do contrato
- Configuração de multa/juros por atraso, carência e custo de reposição de combustível

### Manutenção (`manutencao`)
- Ordens de serviço: preventiva · corretiva · sinistro
- Alertas por quilometragem ou data

### Investidores (`investidores`)
- Cadastro de investidores PF/PJ com dados bancários
- Vínculo veículo ↔ investidor com taxa de gestão semanal
- Cobranças de gestão individuais ou em lote
- Badge na sidebar para cobranças vencidas

### Core (`core`)
- Dashboard com visão geral da frota, devoluções do dia e alertas
- Relatórios: Contratos · Frota/Ocupação · DRE · Clientes · Inadimplência
- Exportação CSV
- Busca global

---

## Grupos de permissão

| Grupo | Acesso |
|---|---|
| `admin_locadora` | Total — todos os módulos |
| `atendente` | Clientes, reservas e contratos (sem exclusão) |
| `financeiro` | Módulo financeiro e relatórios (sem exclusão) |
| `mecanico` | Manutenção e frota (sem exclusão) |

---

## Configuração do ambiente

### 1. Pré-requisitos

- Python 3.12
- Redis (opcional, apenas para Celery)

### 2. Clonar e criar o ambiente virtual

```bash
git clone <repositorio>
cd "ALUGA FÁCIL"
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### 3. Variáveis de ambiente

Copie `.env.example` para `.env` e ajuste os valores conforme necessário. Em desenvolvimento, o banco é SQLite e não exige configuração adicional.

### 4. Banco de dados

```bash
python manage.py migrate
```

### 5. Grupos de permissão e superusuário

```bash
python manage.py criar_grupos
python manage.py createsuperuser
```

### 6. Dados de demonstração (opcional)

```bash
python manage.py popular_dados_teste
# Para limpar antes de repopular:
python manage.py popular_dados_teste --limpar
```

O comando cria 10 veículos, 8 clientes, 8 contratos em todos os estados, reservas, pagamentos, despesas, multas, ordens de manutenção e usuários com todos os grupos.

### 7. Arquivos estáticos

```bash
python manage.py collectstatic
```

### 8. Iniciar o servidor

```bash
# Django dev server
python manage.py runserver

# Celery (em terminal separado, opcional)
celery -A config worker -l info
```

Acesse em `http://localhost:8000`.

---

## Estrutura do projeto

```
ALUGA FÁCIL/
├── apps/
│   ├── core/           # Dashboard, relatórios, mixins, middleware
│   ├── fleet/          # Frota de veículos
│   ├── customers/      # Clientes e CNH
│   ├── contracts/      # Reservas, contratos e pagamentos
│   ├── financeiro/     # Contas, despesas e multas
│   ├── manutencao/     # Ordens de serviço e alertas
│   └── investidores/   # Gestão de investidores
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   ├── celery.py
│   ├── asgi.py
│   └── wsgi.py
├── templates/
├── static/
├── media/
├── manage.py
├── requirements.txt
└── .env
```

---

## Settings

O projeto usa `DJANGO_SETTINGS_MODULE=config.settings.development` por padrão. Para produção, `config/settings/production.py` herda de `base.py` e usa PostgreSQL — configure `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `SECRET_KEY` e demais variáveis via `.env`. O arquivo `settings.py` na raiz é o ponto de entrada exigido pelo PythonAnywhere e apenas importa `config/settings/production.py`.
"# alugafacil"
# Aluga-Facil-Sistem-de-Gest-o
