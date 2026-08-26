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
  Invoke-FleetSsh $WH2 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul'
  Invoke-FleetSsh $WH1 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul'
  Invoke-FleetSsh $WH3 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul'
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

Write-Host '=== START REMOTE WORKERS ===' -ForegroundColor Green
Invoke-FleetSsh $WH1 'powershell -NoProfile -ExecutionPolicy Bypass -File D:\re1_rl\_tmp\_restart_wh1_pl.ps1'
Invoke-FleetSsh $WH2 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\sshuser\re1_rl\fleet\local\restart_worker_workhorse2_planner_loyal.ps1'
& cmd.exe /c (Join-Path $ROOT 'fleet\local\start_worker_detached_pking_planner_loyal.cmd')
Write-Host 'PKING planner-loyal worker started.'

Write-Host 'PLANNER_LOYAL_FLEET_RESTART_OK' -ForegroundColor Green
