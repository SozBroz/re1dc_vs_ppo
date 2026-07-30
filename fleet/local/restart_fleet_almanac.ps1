# Lean fleet restart: tear down (optional), sync (optional), fire starts, exit.
# Does NOT sit for minutes waiting on learner HTTP / 28-emu warmup.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File fleet\local\restart_fleet_almanac.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File fleet\local\restart_fleet_almanac.ps1 -SkipTeardown -SkipSync
param(
  [switch]$SkipTeardown,
  [switch]$SkipSync,
  [switch]$SmokeCheck
)

$ErrorActionPreference = 'Stop'
$WH2 = 'sshuser@192.168.0.116'
$WH1 = 'sshuser@192.168.0.203'
$BRANCH = 'feature/doc04-medium-extractor'
$ROOT = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $ROOT

function Invoke-FleetSsh([string]$HostName, [string]$Cmd) {
  Write-Host ">>> $HostName" -ForegroundColor Cyan
  & ssh.exe -o ConnectTimeout=15 -o BatchMode=yes $HostName $Cmd
  if ($LASTEXITCODE -ne 0) { throw "ssh failed ($LASTEXITCODE): $HostName" }
}

if (-not $SkipTeardown) {
  Write-Host '=== TEARDOWN ===' -ForegroundColor Yellow
  $teardown = Join-Path $ROOT '_tmp_fleet_teardown.ps1'
  if (Test-Path $teardown) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $teardown
  } else {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
      Where-Object { $_.CommandLine -match 'distributed_train' } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
    Stop-Process -Name EmuHawk -Force -EA SilentlyContinue
  }
}

if (-not $SkipSync) {
  Write-Host '=== SYNC ===' -ForegroundColor Yellow
  git fetch origin $BRANCH
  git checkout $BRANCH
  git pull --ff-only origin $BRANCH
  Write-Host ("PKING={0}" -f (git rev-parse --short HEAD))
  Invoke-FleetSsh $WH2 "cd /d C:\Users\sshuser\re1_rl && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"
  Invoke-FleetSsh $WH1 "cd /d D:\re1_rl && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"
}

Write-Host '=== START (fire and forget) ===' -ForegroundColor Green
$wh2Start = Join-Path $ROOT '_tmp_start_wh2_now.ps1'
$wh1Start = Join-Path $ROOT '_tmp_start_wh1_now.ps1'
if (-not (Test-Path $wh2Start) -or -not (Test-Path $wh1Start)) {
  throw "missing start scripts: $wh2Start / $wh1Start"
}
& scp.exe -o ConnectTimeout=10 $wh2Start "${WH2}:C:/Users/sshuser/re1_rl/_tmp_start_wh2_now.ps1"
& scp.exe -o ConnectTimeout=10 $wh1Start "${WH1}:D:/re1_rl/_tmp_start_wh1_now.ps1"
Invoke-FleetSsh $WH2 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\sshuser\re1_rl\_tmp_start_wh2_now.ps1'
Invoke-FleetSsh $WH1 'powershell -NoProfile -ExecutionPolicy Bypass -File D:\re1_rl\_tmp_start_wh1_now.ps1'
& cmd.exe /c (Join-Path $ROOT 'fleet\local\start_worker_detached_pking_visible.cmd')
Write-Host 'PKING_STARTED_VISIBLE'

if ($SmokeCheck) {
  # One optional probe only — do not loop for minutes.
  Start-Sleep -Seconds 8
  try {
    $s = (Invoke-WebRequest -UseBasicParsing http://192.168.0.116:8765/status -TimeoutSec 4).Content
    Write-Host $s.Substring(0, [Math]::Min(400, $s.Length))
  } catch {
    Write-Host 'learner HTTP not up yet (emulators still booting) — not waiting'
  }
}

Write-Host 'FLEET_STARTS_FIRED'
