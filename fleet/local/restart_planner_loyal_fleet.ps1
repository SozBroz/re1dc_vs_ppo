# Ordered planner-loyal fleet restart: learner first, wait for HTTP health, then workers.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File fleet\local\restart_planner_loyal_fleet.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File fleet\local\restart_planner_loyal_fleet.ps1 -SkipTeardown -SkipSync
param(
  [switch]$SkipTeardown,
  [switch]$SkipSync,
  [int]$LearnerWaitSec = 600
)

$ErrorActionPreference = 'Stop'
$WH1 = 'sshuser@192.168.0.203'
$WH2 = 'sshuser@192.168.0.116'
$WH3 = 'sshuser@192.168.0.229'
$LEARNER_HOST = '192.168.0.229'
$LEARNER_PORT = 8765
$BRANCH = 'feature/planner-loyal-ppo'
$ROOT = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $ROOT

function Invoke-FleetSsh([string]$HostName, [string]$Cmd) {
  Write-Host ">>> $HostName" -ForegroundColor Cyan
  & ssh.exe -o ConnectTimeout=15 -o BatchMode=yes $HostName $Cmd
  if ($LASTEXITCODE -ne 0) { throw "ssh failed ($LASTEXITCODE): $HostName" }
}

function Wait-LearnerHealth([int]$TimeoutSec) {
  $url = "http://${LEARNER_HOST}:${LEARNER_PORT}/health"
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  Write-Host "Waiting for learner at $url (up to ${TimeoutSec}s)..." -ForegroundColor Yellow
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
      if ($resp.StatusCode -eq 200) {
        Write-Host "Learner healthy." -ForegroundColor Green
        return
      }
    } catch {
      # learner still booting
    }
    Start-Sleep -Seconds 5
  }
  throw "Learner at $url not healthy within ${TimeoutSec}s"
}

if (-not $SkipTeardown) {
  Write-Host '=== TEARDOWN ===' -ForegroundColor Yellow
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ROOT '_tmp\_stop_fleet_procs.ps1')
  # taskkill exits 128 when the image is not running; do not fail the restart.
  Invoke-FleetSsh $WH2 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul & exit 0'
  Invoke-FleetSsh $WH1 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul & exit 0'
  Invoke-FleetSsh $WH3 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul & exit 0'
  Start-Sleep -Seconds 3
}

if (-not $SkipSync) {
  Write-Host '=== SYNC ===' -ForegroundColor Yellow
  git pull --ff-only origin $BRANCH
  Write-Host ("PKING={0}" -f (git rev-parse --short HEAD))
  Invoke-FleetSsh $WH2 "cd /d C:\Users\sshuser\re1_rl && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"
  Invoke-FleetSsh $WH1 "cd /d D:\re1_rl && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"
  Invoke-FleetSsh $WH3 "cd /d C:\Users\sshuser\re1_rl && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"
}

Write-Host '=== START WH3 LEARNER (WMI) ===' -ForegroundColor Green
Invoke-FleetSsh $WH3 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\sshuser\re1_rl\fleet\local\wmi_start_learner_wh3_planner_loyal.ps1'
Wait-LearnerHealth -TimeoutSec $LearnerWaitSec

Write-Host '=== START C-RE1 RECOMP WORKERS (no BizHawk) ===' -ForegroundColor Green
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ROOT '_tmp\_start_recomp_fleet_scaled.ps1')
if ($LASTEXITCODE -ne 0) { throw "recomp fleet start failed ($LASTEXITCODE)" }
Write-Host 'C-RE1 recomp workers started.'

# Gate: all four workers must be visible on the learner (pking used to crash-loop
# and the restart script still printed OK).
$expected = @('pking-recomp', 'wh1-recomp', 'wh2-recomp', 'wh3-recomp')
$gateDeadline = (Get-Date).AddSeconds(120)
Write-Host 'Waiting for all workers on learner /status...' -ForegroundColor Yellow
while ((Get-Date) -lt $gateDeadline) {
  try {
    $st = (Invoke-WebRequest -UseBasicParsing "http://${LEARNER_HOST}:${LEARNER_PORT}/status" -TimeoutSec 5).Content | ConvertFrom-Json
    $missing = @()
    foreach ($wid in $expected) {
      $w = $st.workers.$wid
      $n = 0
      if ($null -ne $w) { $n = [int]$w.n_envs }
      # pking reports n_envs early during staggered boot; require full count.
      $need = switch ($wid) {
        'pking-recomp' { 20 }
        'wh1-recomp' { 8 }
        'wh2-recomp' { 28 }
        'wh3-recomp' { 24 }
        default { 1 }
      }
      if ($n -lt $need) { $missing += ("{0}({1}/{2})" -f $wid, $n, $need) }
    }
    if ($missing.Count -eq 0) {
      Write-Host ('all workers online: ' + (($expected | ForEach-Object { "$_=$($st.workers.$_.n_envs)" }) -join ' ')) -ForegroundColor Green
      Write-Host 'PLANNER_LOYAL_FLEET_RESTART_OK' -ForegroundColor Green
      return
    }
    Write-Host ('  missing: ' + ($missing -join ', '))
  } catch {
    Write-Host '  status poll failed'
  }
  Start-Sleep -Seconds 5
}
throw 'fleet restart incomplete: not all workers visible on learner'
