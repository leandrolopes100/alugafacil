me# Fixtures — Aluga Fácil

Conjunto completo de dados de teste para validação funcional, de integração e de fluxos do sistema.

---

## Estrutura

```
fixtures/
  public/
    01_tenant.json          # TenantCompany + Domain (schema público)
  tenant/
    01_usuarios.json        # 4 usuários (admin, gerente, 2 atendentes)
    02_frota.json           # 5 categorias + 5 grupos + 8 veículos + 12 documentos + 10 histórico km
    03_clientes.json        # 6 clientes (PF/PJ, ativo/bloqueado) + 5 CNHs
    04_configuracao.json    # ConfiguracaoLocadora (multa, juros, caução combustível)
    05_contratos.json       # 7 reservas + 7 contratos + adicionais + avarias + pagamentos + parcelas
    06_contas_receber.json  # 5 ContaReceber (vencido, parcial, pago)
    07_financeiro.json      # 6 despesas + 20 parcelas despesa + 4 multas
    08_manutencao.json      # 5 ordens manutenção + 6 alertas
```

---

## Como Carregar

### Pré-requisito: tenant "demo" deve existir no banco

Se o tenant ainda não existe, crie antes de carregar as fixtures de tenant:

```bash
python manage.py create_tenant --schema_name=demo --name="Auto Flex Locadora Ltda" --slug=demo --plan=professional
python manage.py create_tenant_domain --domain=demo.localhost --tenant=demo
```

Ou carregue a fixture pública (se o schema já estiver criado manualmente no PostgreSQL):

```bash
python manage.py loaddata fixtures/public/01_tenant.json
```

> **Atenção**: `auto_create_schema = True` no model TenantCompany. Em produção, o schema
> é criado automaticamente ao salvar o TenantCompany. Em ambiente de teste use
> `migrate_schemas --schema=demo` após criar o tenant.

---

### Carregar todas as fixtures de tenant (ordem obrigatória)

```powershell
.\venv\Scripts\python.exe manage.py tenant_command loaddata fixtures/tenant/01_usuarios.json --schema=demo
.\venv\Scripts\python.exe manage.py tenant_command loaddata fixtures/tenant/02_frota.json --schema=demo
.\venv\Scripts\python.exe manage.py tenant_command loaddata fixtures/tenant/03_clientes.json --schema=demo
.\venv\Scripts\python.exe manage.py tenant_command loaddata fixtures/tenant/04_configuracao.json --schema=demo
.\venv\Scripts\python.exe manage.py tenant_command loaddata fixtures/tenant/05_contratos.json --schema=demo
.\venv\Scripts\python.exe manage.py tenant_command loaddata fixtures/tenant/06_contas_receber.json --schema=demo
.\venv\Scripts\python.exe manage.py tenant_command loaddata fixtures/tenant/07_financeiro.json --schema=demo
.\venv\Scripts\python.exe manage.py tenant_command loaddata fixtures/tenant/08_manutencao.json --schema=demo
```

> **Ordem importa**: `07_financeiro` deve ser carregado **antes** de `08_manutencao` porque
> `08_manutencao.json` contém as `DespesaOperacional` pk=7 e pk=8 com `os_origem` apontando
> para as OS pk=3 e pk=4 — que precisam existir primeiro.

---

### Script de carga completo (Windows PowerShell)

```powershell
$manage = ".\venv\Scripts\python.exe manage.py"

# Fixture pública (schema público — apenas TenantCompany + Domain)
& $manage loaddata fixtures/public/01_tenant.json

# Fixtures do tenant
$schema = "--schema=demo"
$fixtures = @(
    "fixtures/tenant/01_usuarios.json",
    "fixtures/tenant/02_frota.json",
    "fixtures/tenant/03_clientes.json",
    "fixtures/tenant/04_configuracao.json",
    "fixtures/tenant/05_contratos.json",
    "fixtures/tenant/06_contas_receber.json",
    "fixtures/tenant/07_financeiro.json",
    "fixtures/tenant/08_manutencao.json"
)
foreach ($f in $fixtures) {
    Write-Host "Carregando $f..."
    & $manage tenant_command loaddata $schema $f
}
Write-Host "Fixtures carregadas com sucesso."
```

---

## Credenciais dos Usuários

| Username     | Senha          | Perfil        |
|-------------|----------------|---------------|
| `admin`     | `admin123`     | Superusuário  |
| `gerente`   | `gerente123`   | Staff/Gerente |
| `atendente1`| `atendente123` | Atendente     |
| `atendente2`| `atendente123` | Atendente     |

URL de login: `http://demo.localhost:8000/login/`

---

## Dados de Teste por Módulo

### Frota (8 veículos)

| PK | Placa      | Modelo              | Situação    | Cenário de teste                    |
|----|-----------|---------------------|-------------|-------------------------------------|
| 1  | ABC1D23   | Chevrolet Onix      | Disponível  | Checkout disponível, histórico km   |
| 2  | DEF5G67   | Hyundai HB20        | Disponível  | Contrato devolvido aguardando fecha.|
| 3  | GHI9J01   | Chevrolet Cruze     | Em uso      | Contrato ativo em atraso            |
| 4  | JKL3M45   | Toyota Corolla      | Em uso      | Contrato ativo normal, multa ativa  |
| 5  | MNO7P89   | Hyundai Creta       | Reservado   | Contrato aberto, aguarda checkout   |
| 6  | PQR1S23   | Jeep Renegade       | Disponível  | Contrato encerrado com km excedente |
| 7  | STU4V56   | Chevrolet S10       | Manutenção  | OS em andamento + OS agendada       |
| 8  | VWX7Y89   | Citroën C3          | Inativo     | Veículo desativado (alto km)        |

**Documentos de borda**: CRLV do C3 (pk=8) vencido; seguro da Creta próximo ao vencimento; extintor da S10 vencido.

### Clientes (6)

| PK | Nome                     | Tipo | CPF/CNPJ          | Situação  |
|----|-------------------------|------|-------------------|-----------|
| 1  | João Carlos Silva       | PF   | 529.982.247-25    | Ativo     |
| 2  | Maria Fernanda Souza    | PF   | 871.920.321-09    | Ativo     |
| 3  | Carlos Eduardo Oliveira | PF   | 111.444.777-35    | Ativo     |
| 4  | Tech Solutions (PJ)     | PJ   | 34.567.890/0001-18| Ativo     |
| 5  | Fernanda Lima Pereira   | PF   | 012.345.678-90    | Bloqueado |
| 6  | Roberto Costa Almeida   | PF   | 321.654.987-09    | Ativo     |

CNH vencida: Fernanda Lima (pk=4, validade 2024-04-08)

### Contratos (7) — todos os estados cobertos

| Número         | Situação              | Cliente        | Veículo | Cenário especial                    |
|----------------|-----------------------|----------------|---------|-------------------------------------|
| AF-2026-0001   | **Ativo (em atraso)** | João Silva     | Cruze   | Venceu 27/05, avaria cobrada        |
| AF-2026-0002   | **Ativo**             | Tech Solutions | Corolla | Pagamento parcial, multa pendente   |
| AF-2026-0003   | **Aberto**            | Carlos Oliveira| Creta   | Checkout pendente, caução pendente  |
| AF-2026-0004   | **Em Fechamento**     | Maria Souza    | HB20    | 8 dias extras, dif. combustível     |
| AF-2025-0001   | **Encerrado**         | Roberto Costa  | Onix    | Devolvido antes do prazo            |
| AF-2025-0002   | **Encerrado**         | João Silva     | Renegade| KM excedente 450km                  |
| AF-2026-0005   | **Cancelado**         | Fernanda Lima  | Onix    | Cliente bloqueado, sem checkout     |

### Reservas (7) — todos os estados cobertos

Pendente, Confirmada, Ativa (2×), Concluída (2×), No Show, Cancelada.

### Financeiro

**Despesas (6 manuais + 2 auto-geradas pelas OS)**:
- IPVA Cruze (pago)
- Seguro frota 12× (4 pagas, 8 pendentes)
- Aluguel escritório 6× débito automático (4 pagas, 2 pendentes)
- Combustível Onix (pendente)
- Lavagem frota (pago)
- Salário atendente 2× (1 paga, 1 pendente)
- Revisão Onix OS#3 (auto-gerada — R$ 380)
- Revisão Corolla OS#4 (auto-gerada — R$ 520)

**Multas**: pendente_identificacao, identificada, cobrada_cliente, paga.

**Contas a Receber**: vencido (0%), pago_parcial (2×), pago (2×).

### Manutenção

| OS | Veículo | Tipo       | Situação      | Cenário                          |
|----|---------|------------|---------------|----------------------------------|
| 1  | S10     | Preventiva | Agendada      | Revisão futura                   |
| 2  | S10     | Corretiva  | Em andamento  | Veículo imobilizado              |
| 3  | Onix    | Preventiva | Concluída     | Gera DespesaOperacional (R$380)  |
| 4  | Corolla | Preventiva | Concluída     | Gera DespesaOperacional (R$520)  |
| 5  | HB20    | Preventiva | Cancelada     | Cancelamento sem execução        |

**Alertas de manutenção**:
- S10 pk=5: VENCIDO (km_atual=112k > km_servico=110k)
- HB20 pk=2: PRÓXIMO (km_atual=32950, próximo=40000 — dentro do intervalo de alerta 500km? não, mas fornece estado para testar)
- Corolla pk=4: Por data — próximo (2026-07-01)
- Renegade pk=6: Por data — futuro (2026-08-15)

---

## Observações Técnicas

### Sobre OrdemManutencao e DespesaOperacional
`loaddata` usa `save_base(raw=True)` que **bypassa o `save()` customizado** de todos os models.
Portanto, o método `_sincronizar_despesa()` da `OrdemManutencao` **nunca dispara** durante a
carga dos fixtures.

As `DespesaOperacional` vinculadas a OS (pk=7 → OS#3, pk=8 → OS#4) estão declaradas
**explicitamente** em `08_manutencao.json`, após as OS que elas referenciam via `os_origem`.

Mesma regra se aplica a: `Contrato.numero` (não é auto-gerado — incluso no fixture),
`auto_now_add`/`auto_now` (não são preenchidos — todos os fixtures incluem `criado_em`/`atualizado_em`).

### Senhas
Hashes PBKDF2-SHA256 com 1.000.000 iterações (Django 5.2 padrão).
Para redefinir senhas em desenvolvimento:
```bash
python manage.py tenant_command changepassword --schema=demo admin
```

### Dados adicionais para volume
Para testes de paginação e relatórios com maior volume de dados, use o script:
```bash
python manage.py tenant_command shell --schema=demo
```
E execute scripts Python para criar dados adicionais respeitando as regras de negócio.
