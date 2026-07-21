# Script de setup do banco de dados - Aluga Facil
# Uso: .\setup_banco.ps1
# Sera pedida a senha do postgres interativamente

$PSQL = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
$DB_NOME = "alugafacil"
$DB_USUARIO = "alugafacil"
$DB_SENHA = "alugafacil123"

Write-Host "=== Aluga Facil - Setup do Banco de Dados ===" -ForegroundColor Cyan
Write-Host ""

# Pede senha do postgres
$senhaPg = Read-Host "Digite a senha do usuario postgres" -AsSecureString
$senhaTexto = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($senhaPg)
)
$env:PGPASSWORD = $senhaTexto

Write-Host ""
Write-Host "[1/4] Criando usuario '$DB_USUARIO'..." -ForegroundColor Yellow
& $PSQL -U postgres -c "CREATE USER $DB_USUARIO WITH PASSWORD '$DB_SENHA';" 2>&1 | Where-Object { $_ -notmatch "^$" }

Write-Host "[2/4] Criando banco '$DB_NOME'..." -ForegroundColor Yellow
& $PSQL -U postgres -c "CREATE DATABASE $DB_NOME OWNER $DB_USUARIO ENCODING 'UTF8' LC_COLLATE 'Portuguese_Brazil.1252' LC_CTYPE 'Portuguese_Brazil.1252' TEMPLATE template0;" 2>&1 | Where-Object { $_ -notmatch "^$" }

if ($LASTEXITCODE -ne 0) {
    Write-Host "  -> Tentando com locale padrao..." -ForegroundColor Gray
    & $PSQL -U postgres -c "CREATE DATABASE $DB_NOME OWNER $DB_USUARIO;" 2>&1 | Where-Object { $_ -notmatch "^$" }
}

Write-Host "[3/4] Concedendo privilegios..." -ForegroundColor Yellow
& $PSQL -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NOME TO $DB_USUARIO;" 2>&1 | Where-Object { $_ -notmatch "^$" }
& $PSQL -U postgres -c "ALTER USER $DB_USUARIO CREATEDB;" 2>&1 | Where-Object { $_ -notmatch "^$" }

$env:PGPASSWORD = ""

Write-Host "[4/4] Verificando conexao..." -ForegroundColor Yellow
$env:PGPASSWORD = $DB_SENHA
$teste = & $PSQL -U $DB_USUARIO -d $DB_NOME -c "SELECT version();" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=== Banco criado com sucesso! ===" -ForegroundColor Green
    Write-Host ""
    Write-Host "Agora rode as migrations:" -ForegroundColor Cyan
    Write-Host "  .\venv\Scripts\python manage.py migrate_schemas --shared" -ForegroundColor White
    Write-Host "  .\venv\Scripts\python manage.py create_tenant" -ForegroundColor White
    Write-Host "  .\venv\Scripts\python manage.py runserver" -ForegroundColor White
} else {
    Write-Host ""
    Write-Host "ERRO ao conectar. Verifique a senha e tente novamente." -ForegroundColor Red
    Write-Host $teste -ForegroundColor Red
}
$env:PGPASSWORD = ""
