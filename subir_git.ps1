# subir_git.ps1
# Uso:
#   .\subir_git.ps1                          # mensaje automatico con fecha y hora
#   .\subir_git.ps1 -Mensaje "Agrego X"      # mensaje propio
#   .\subir_git.ps1 -Remoto "https://github.com/usuario/repo.git"
#   .\subir_git.ps1 -Rama main
#
# Hace: init (si hace falta) -> add -> revision de secretos -> commit -> push

param(
    [string]$Mensaje = "",
    [string]$Remoto = "",
    [string]$Rama = "main"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Ejecutar-Git {
    param([string[]]$Argumentos)
    & git @Argumentos
    if ($LASTEXITCODE -ne 0) { throw "git $($Argumentos -join ' ') fallo (codigo $LASTEXITCODE)" }
}

# 1. Inicializar repositorio si no existe
& git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[*] No es un repositorio. Inicializando..." -ForegroundColor Cyan
    Ejecutar-Git @("init", "-b", $Rama)
}

# 2. Configurar remoto si no existe
if (-not ((& git remote) -contains "origin")) {
    if ($Remoto -eq "") {
        $Remoto = Read-Host "Pega la URL del repositorio remoto (vacio = solo local)"
    }
    if ($Remoto -ne "") {
        Ejecutar-Git @("remote", "add", "origin", $Remoto)
        Write-Host "[+] Remoto 'origin' configurado." -ForegroundColor Green
    }
}

# 3. Agregar archivos (el .gitignore excluye config.json, .env, tokens/ y state/)
Ejecutar-Git @("add", "-A")

# 4. Revision de secretos en lo que se va a subir
#    Los archivos *.example se omiten: son plantillas con valores vacios o de ejemplo.
$patronesSecreto = @(
    'password"?\s*[:=]\s*["''][^"'']{4,}["'']',
    '(access|refresh)_token"?\s*[:=]\s*["''][^"'']{20,}["'']',
    'client_secret"?\s*[:=]\s*["''][^"'']{8,}["'']',
    'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY'
)
$patronCorreo = '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
$dominiosPermitidos = 'ejemplo\.com|example\.com'

$archivos = @(& git diff --cached --name-only)
$hallazgos = @()
foreach ($f in $archivos) {
    if ($f -match '\.example$') { continue }
    if (-not (Test-Path $f)) { continue }
    $n = 0
    foreach ($linea in (Get-Content -Path $f -Encoding UTF8 -ErrorAction SilentlyContinue)) {
        $n++
        foreach ($p in $patronesSecreto) {
            if ($linea -match $p) { $hallazgos += "${f}:${n}  (posible credencial)" }
        }
        if (($linea -match $patronCorreo) -and -not ($linea -match $dominiosPermitidos)) {
            $hallazgos += "${f}:${n}  (direccion de correo real)"
        }
    }
}
if ($hallazgos.Count -gt 0) {
    Write-Host "[-] Se detectaron datos sensibles. Se cancela el commit:" -ForegroundColor Red
    $hallazgos | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
    Write-Host "Mueve esos datos a .env o config.json (ignorados por Git) y vuelve a ejecutar." -ForegroundColor Red
    & git reset -q
    exit 1
}

# 5. Commit si hay cambios
& git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "[*] No hay cambios nuevos para commitear." -ForegroundColor Cyan
} else {
    if ($Mensaje -eq "") { $Mensaje = "Actualizacion " + (Get-Date -Format "yyyy-MM-dd HH:mm") }
    Ejecutar-Git @("commit", "-m", $Mensaje)
    Write-Host "[+] Commit creado: $Mensaje" -ForegroundColor Green
}

# 6. Push si hay remoto
if ((& git remote) -contains "origin") {
    & git rev-parse --verify HEAD *> $null
    if ($LASTEXITCODE -eq 0) {
        Ejecutar-Git @("push", "-u", "origin", $Rama)
        Write-Host "[+] Subido a origin/$Rama" -ForegroundColor Green
    }
} else {
    Write-Host "[*] Sin remoto configurado: solo commit local." -ForegroundColor Cyan
}
