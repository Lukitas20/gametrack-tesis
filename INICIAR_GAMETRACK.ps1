# Desde la terminal de VS Code: .\INICIAR_GAMETRACK.ps1
# Respeta la configuración de backend/.env y las variables del entorno.
$ErrorActionPreference = 'Stop'
$backend = Join-Path $PSScriptRoot 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Primero prepará el entorno virtual como indica README.md.' }
Push-Location -LiteralPath $backend
try {
    Write-Output 'GameTrack: http://localhost:8000/#/cuentas (Ctrl+C para detener)'
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
} finally {
    Pop-Location
}
