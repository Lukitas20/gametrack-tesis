# Cargar con: . ./deploy/google-cloud/session.ps1
# Herramientas y credenciales aisladas del resto de proyectos de esta PC.
$ErrorActionPreference = 'Stop'
$gameTrackRepo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$gameTrackTools = Join-Path $PSScriptRoot '.tools'
$gameTrackGcloud = Join-Path $gameTrackTools 'google-cloud-sdk/bin/gcloud.cmd'
$gameTrackTerraform = Join-Path $gameTrackTools 'terraform.exe'
if (-not (Test-Path -LiteralPath $gameTrackGcloud)) {
    throw 'Falta la copia local de Google Cloud CLI en deploy/google-cloud/.tools.'
}
if (-not (Test-Path -LiteralPath $gameTrackTerraform)) {
    throw 'Falta la copia local de Terraform en deploy/google-cloud/.tools.'
}
$env:CLOUDSDK_CONFIG = Join-Path $gameTrackTools 'gcloud-config'
$env:CLOUDSDK_PYTHON = Join-Path $gameTrackRepo 'backend/.venv-local/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $env:CLOUDSDK_PYTHON)) {
    $env:CLOUDSDK_PYTHON = Join-Path $gameTrackTools 'google-cloud-sdk/platform/bundledpython/python.exe'
}
$env:CLOUDSDK_CORE_PROJECT = 'gametrack-510920'
$env:CLOUDSDK_CORE_DISABLE_USAGE_REPORTING = 'true'
$env:CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK = 'true'
$gameTrackAdc = Join-Path $env:CLOUDSDK_CONFIG 'application_default_credentials.json'
if (Test-Path -LiteralPath $gameTrackAdc) {
    $env:GOOGLE_APPLICATION_CREDENTIALS = $gameTrackAdc
}
Set-Alias -Name gcloud -Value $gameTrackGcloud -Scope Global
Set-Alias -Name terraform -Value $gameTrackTerraform -Scope Global
$gameTrackSdkBin = Split-Path -Parent $gameTrackGcloud
if (($env:PATH -split ';') -notcontains $gameTrackSdkBin) {
    # Permite a los helpers Python encontrar gcloud sin cambiar el PATH global.
    $env:PATH = $gameTrackSdkBin + ';' + $env:PATH
}
