# start-mu2e-shift-scheduler.ps1 -- install/update dependencies and start the server (Windows)
#
# Usage:
#   .\scripts\start-mu2e-shift-scheduler.ps1 [-AdminPassword <pass>] [-BindHost <host>]
#       [-Port <port>] [-DebugServer] [-NoUpdate]
param(
    [string]$AdminPassword,
    [string]$AdminEmail,
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8001,
    [switch]$DebugServer,
    [switch]$NoUpdate
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$Py = Join-Path $ProjectDir "venv\Scripts\python.exe"
$PidFile = Join-Path $ScriptDir ".mu2e-shift-scheduler.pid"
$LogDir = Join-Path $ScriptDir "logs"
$LogFile = Join-Path $LogDir "mu2e-shift-scheduler.log"

if (-not (Test-Path $Py)) {
    Write-Host "==> Creating virtual environment in venv\..." -ForegroundColor Cyan
    python -m venv (Join-Path $ProjectDir "venv")
}

if (-not $NoUpdate) {
    Write-Host "==> Installing/updating dependencies..." -ForegroundColor Cyan
    & $Py -m pip install --quiet --upgrade pip
    & $Py -m pip install --quiet -r (Join-Path $ProjectDir "requirements-dev.txt")
    & $Py -m pip install --quiet -e $ProjectDir
}

if ($AdminPassword) { $env:MU2E_INITIAL_ADMIN_PASSWORD = $AdminPassword }
if ($AdminEmail) { $env:MU2E_INITIAL_ADMIN_EMAIL = $AdminEmail }
if (-not $env:SHOW_ADMIN_LOGIN) { $env:SHOW_ADMIN_LOGIN = "1" }
if (-not $env:SESSION_COOKIE_SECURE) { $env:SESSION_COOKIE_SECURE = "0" }

# Stop a previous instance
if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile
    if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) {
        Write-Host "==> Server already running (PID $OldPid) -- restarting..." -ForegroundColor Cyan
        Stop-Process -Id $OldPid -Force
    }
    Remove-Item $PidFile -Force
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$RunArgs = @("run.py", "--host", $BindHost, "--port", $Port)
if ($DebugServer) { $RunArgs += "--debug" }

Write-Host "==> Starting server on ${BindHost}:${Port}..." -ForegroundColor Cyan
$Process = Start-Process -FilePath $Py -ArgumentList $RunArgs -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $LogFile -RedirectStandardError "$LogFile.err" -PassThru -WindowStyle Hidden
$Process.Id | Out-File $PidFile

Start-Sleep -Seconds 2
if (Get-Process -Id $Process.Id -ErrorAction SilentlyContinue) {
    Write-Host "[OK] Server running (PID $($Process.Id)) -- http://${BindHost}:${Port}/" -ForegroundColor Green
    Write-Host "     Stop with: .\scripts\stop-mu2e-shift-scheduler.ps1"
} else {
    Write-Host "[ERROR] Server exited during startup. See $LogFile" -ForegroundColor Red
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    exit 1
}
