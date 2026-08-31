# stop-mu2e-shift-scheduler.ps1 -- stop the running Mu2e Shift Scheduler server (Windows)
#
# Usage:
#   .\scripts\stop-mu2e-shift-scheduler.ps1 [-Tail]
param([switch]$Tail)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $ScriptDir ".mu2e-shift-scheduler.pid"
$LogFile = Join-Path $ScriptDir "logs\mu2e-shift-scheduler.log"

if ($Tail -and (Test-Path $LogFile)) {
    Write-Host "==> Last 20 lines of server log:" -ForegroundColor Cyan
    Get-Content $LogFile -Tail 20
}

if (-not (Test-Path $PidFile)) {
    Write-Host "[WARN] No PID file found -- server may not be running." -ForegroundColor Yellow
    exit 0
}

$ServerPid = Get-Content $PidFile
if (Get-Process -Id $ServerPid -ErrorAction SilentlyContinue) {
    Write-Host "==> Stopping server (PID $ServerPid)..." -ForegroundColor Cyan
    Stop-Process -Id $ServerPid
    Start-Sleep -Seconds 2
    if (Get-Process -Id $ServerPid -ErrorAction SilentlyContinue) {
        Stop-Process -Id $ServerPid -Force
    }
    Write-Host "[OK] Server stopped" -ForegroundColor Green
} else {
    Write-Host "[WARN] Process $ServerPid is no longer running (stale PID file removed)" -ForegroundColor Yellow
}
Remove-Item $PidFile -Force
