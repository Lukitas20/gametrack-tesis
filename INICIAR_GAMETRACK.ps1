# Desde la terminal de VS Code: .\INICIAR_GAMETRACK.ps1
# Usa la base runtime existente de esta instalación, sin reiniciar datos.
$ErrorActionPreference = 'Stop'
$backend = Join-Path $PSScriptRoot 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Primero prepará el entorno virtual como indica README.md.' }
$runtimeDb = Join-Path $backend 'gametrack.runtime.db'
$previousDatabase = $env:DATABASE_URL
$previousOrigin = $env:PUBLIC_BASE_URL
Push-Location -LiteralPath $backend
try {
    if (Test-Path -LiteralPath $runtimeDb) {
        $env:DATABASE_URL = 'sqlite:///' + $runtimeDb.Replace('\', '/')
    }
    $env:PUBLIC_BASE_URL = 'http://localhost:8000'
    Write-Output 'GameTrack: http://localhost:8000/#/cuentas (Ctrl+C para detener)'
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
} finally {
    Pop-Location
    $env:DATABASE_URL = $previousDatabase
    $env:PUBLIC_BASE_URL = $previousOrigin
}
