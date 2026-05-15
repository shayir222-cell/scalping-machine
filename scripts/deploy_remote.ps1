# Deploy latest origin/main to scalping-machine VPS via SSH.
#
# Usage from repo root:
#   .\scripts\deploy_remote.ps1            # checks push state, asks if behind
#   .\scripts\deploy_remote.ps1 -Force     # skips push-state check
#
# Prerequisites:
#   - SSH key at $env:USERPROFILE\.ssh\id_ed25519
#   - Public part in /root/.ssh/authorized_keys on VPS
#   - You ran 'git push origin main' first (or use -Force)

param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$VPS_HOST = "root@209.250.234.36"
$KEY = "$env:USERPROFILE\.ssh\id_ed25519"
$APP_DIR = "/home/app/scalping-machine"
$SERVICE = "scalping"
$LOCAL_STATUS_URL = "http://127.0.0.1:8002/status"
$EXT_STATUS_URL = "https://209.250.234.36.sslip.io/status"

if (-not (Test-Path $KEY)) {
    Write-Host "ERROR: SSH key not found at $KEY" -ForegroundColor Red
    exit 1
}

$local = (git rev-parse HEAD).Trim()
$remote = ""
try {
    $remote = (git rev-parse origin/main 2>$null).Trim()
} catch {
    $remote = "(fetch failed)"
}

if ($local -ne $remote -and -not $Force) {
    Write-Host "local HEAD  = $local" -ForegroundColor Yellow
    Write-Host "origin/main = $remote"
    Write-Host ""
    Write-Host "These differ. Either push first, or type 'y' to deploy anyway"
    Write-Host "(the VPS will pull whatever origin/main has now):"
    $r = Read-Host "continue? [y/N]"
    if ($r -ne "y") { exit 1 }
}

Write-Host ""
Write-Host "==> Deploying $($local.Substring(0,7)) to $VPS_HOST" -ForegroundColor Cyan
Write-Host ""

$remoteScript = @"
set -e
echo '--- git pull ---'
sudo -u app git -C $APP_DIR pull
echo
echo '--- systemctl restart $SERVICE ---'
systemctl restart $SERVICE
sleep 3
echo
echo '--- journalctl (last 25 lines) ---'
journalctl -u $SERVICE -n 25 --no-pager
echo
echo '--- /status smoke test (internal) ---'
curl -sS --max-time 5 $LOCAL_STATUS_URL || echo 'INTERNAL /status FAILED'
echo
"@

$remoteScript | ssh -i $KEY `
    -o StrictHostKeyChecking=accept-new `
    -o ConnectTimeout=10 `
    $VPS_HOST 'bash -s'

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "==> Deploy FAILED (ssh exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "==> Verifying from outside ($EXT_STATUS_URL)..." -ForegroundColor Cyan
try {
    $resp = Invoke-WebRequest -Uri $EXT_STATUS_URL -TimeoutSec 15 -UseBasicParsing
    Write-Host "    external /status: HTTP $($resp.StatusCode)" -ForegroundColor Green
    Write-Host "    $($resp.Content)"
} catch {
    Write-Host "    external /status check failed: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "    (internal smoke test above is authoritative)"
}

Write-Host ""
Write-Host "==> Deploy completed" -ForegroundColor Green
