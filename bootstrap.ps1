# bootstrap.ps1 -- first-time setup for the Mu2e Shift Scheduler (Windows)
#
# Usage:
#   .\bootstrap.ps1 [-AdminPassword <pass>] [-NoServer] [-NoTests]
param(
    [string]$AdminPassword = $env:MU2E_INITIAL_ADMIN_PASSWORD,
    [switch]$NoServer,
    [switch]$NoTests
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir "venv"
$Py = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "== Mu2e Shift Scheduler Bootstrap ==" -ForegroundColor Cyan

if (-not (Test-Path $Py)) {
    Write-Host "==> Creating virtual environment in venv\..." -ForegroundColor Cyan
    python -m venv $VenvDir
}

Write-Host "==> Installing/updating dependencies..." -ForegroundColor Cyan
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet -r (Join-Path $ProjectDir "requirements-dev.txt")
& $Py -m pip install --quiet -e $ProjectDir
Write-Host "[OK] Dependencies installed" -ForegroundColor Green

$EnvFile = Join-Path $ProjectDir ".env"
$EnvExample = Join-Path $ProjectDir ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvFile
    Write-Host "[OK] Created .env from .env.example -- edit it to configure the instance" -ForegroundColor Green
}

if (-not $NoTests) {
    Write-Host "==> Running test suite..." -ForegroundColor Cyan
    & $Py -m pytest (Join-Path $ProjectDir "tests") -q
    if ($LASTEXITCODE -ne 0) { throw "Test suite failed" }
    Write-Host "[OK] All tests passed" -ForegroundColor Green
}

if (-not $NoServer) {
    $StartArgs = @("-NoUpdate")
    if ($AdminPassword) { $StartArgs += @("-AdminPassword", $AdminPassword) }
    & (Join-Path $ProjectDir "scripts\start-mu2e-shift-scheduler.ps1") @StartArgs
} else {
    Write-Host "[OK] Bootstrap complete. Start with scripts\start-mu2e-shift-scheduler.ps1" -ForegroundColor Green
}
