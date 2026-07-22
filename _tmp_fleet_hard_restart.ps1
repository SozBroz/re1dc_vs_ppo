$ErrorActionPreference = 'Stop'
$WH2 = 'sshuser@192.168.0.116'
$WH1 = 'sshuser@192.168.0.203'
$BRANCH = 'feature/world-almanac-extractor'
$ROOT = 'D:\re1_rl'
Set-Location $ROOT

function Invoke-FleetSsh([string]$h, [string]$cmd) {
  Write-Host "`n>>> $h :: $cmd" -ForegroundColor Cyan
  & ssh.exe -o ConnectTimeout=15 -o ServerAliveInterval=10 $h $cmd
  if ($LASTEXITCODE -ne 0) { throw "ssh failed ($LASTEXITCODE): $h" }
}

Write-Host '=== TEARDOWN PKING ===' -ForegroundColor Yellow
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
  Where-Object { $_.CommandLine -match 'distributed_train|monitor_human_rewards|play_human' } |
  ForEach-Object { Write-Host "kill py $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Stop-Process -Name EmuHawk -Force -EA SilentlyContinue
@(5755..5780) + @(7788) | ForEach-Object {
  Get-NetTCPConnection -LocalPort $_ -State Listen -EA SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }
}
# Clear wedged PB sync locks on pking before relaunch.
$pbChamps = Join-Path $ROOT 'states\pb\champions'
if (Test-Path $pbChamps) {
  Get-ChildItem $pbChamps -Directory -EA SilentlyContinue | ForEach-Object {
    Remove-Item -Force (Join-Path $_.FullName 'champion.sync.lock') -EA SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $_.FullName '.incoming') -EA SilentlyContinue
  }
}
Start-Sleep 2
Write-Host ("pking leftover emu={0}" -f @(Get-Process EmuHawk -EA SilentlyContinue).Count)

Write-Host '=== TEARDOWN REMOTES ===' -ForegroundColor Yellow
& scp.exe -o ConnectTimeout=15 "$ROOT\_tmp_hard_stop_remote.ps1" "${WH2}:C:/Users/sshuser/re1_rl/_tmp_hard_stop_remote.ps1"
& scp.exe -o ConnectTimeout=15 "$ROOT\_tmp_hard_stop_remote.ps1" "${WH1}:D:/re1_rl/_tmp_hard_stop_remote.ps1"
Invoke-FleetSsh $WH2 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\sshuser\re1_rl\_tmp_hard_stop_remote.ps1 -Role wh2'
Invoke-FleetSsh $WH1 'powershell -NoProfile -ExecutionPolicy Bypass -File D:\re1_rl\_tmp_hard_stop_remote.ps1 -Role wh1'

Write-Host '=== SYNC ===' -ForegroundColor Yellow
git fetch origin $BRANCH
git checkout $BRANCH
git pull --ff-only origin $BRANCH
Write-Host ("PKING head={0}" -f (git rev-parse --short HEAD))
Invoke-FleetSsh $WH2 "cd /d C:\Users\sshuser\re1_rl && git fetch origin $BRANCH && git checkout $BRANCH && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"
Invoke-FleetSsh $WH1 "cd /d D:\re1_rl && git fetch origin $BRANCH && git checkout $BRANCH && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"

Write-Host '=== START WH2 ===' -ForegroundColor Green
$wh2Start = @'
$ErrorActionPreference = "Continue"
Set-Location C:\Users\sshuser\re1_rl
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
"[$(Get-Date -Format o)] hard restart" | Out-File data\logs\learner_wh2_25.log -Append -Encoding utf8
$tn = "RE1_almanac_learner"
$tr = 'C:\Users\sshuser\re1_rl\fleet\local\run_distributed_learner_wh2_25.cmd'
schtasks /Delete /TN $tn /F 2>$null | Out-Null
schtasks /Create /TN $tn /TR $tr /SC ONCE /ST 00:00 /RL HIGHEST /F | Out-Host
schtasks /Run /TN $tn | Out-Host
Start-Sleep 12
$py = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue | Where-Object { $_.CommandLine -match "distributed_train" })
Write-Output ("WH2_START py={0} head={1}" -f $py.Count, (git rev-parse --short HEAD))
'@
Set-Content (Join-Path $env:TEMP 'wh2_start.ps1') $wh2Start -Encoding UTF8
& scp.exe -o ConnectTimeout=15 (Join-Path $env:TEMP 'wh2_start.ps1') "${WH2}:C:/Users/sshuser/re1_rl/_tmp_hard_start.ps1"
Invoke-FleetSsh $WH2 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\sshuser\re1_rl\_tmp_hard_start.ps1'

Write-Host '=== WAIT LEARNER ===' -ForegroundColor Green
$up = $false
for ($i = 0; $i -lt 48; $i++) {
  Start-Sleep 5
  try {
    $s = (Invoke-WebRequest -UseBasicParsing http://192.168.0.116:8765/status -TimeoutSec 4).Content
    Write-Host ("learner up t={0}s" -f ($i * 5))
    Write-Host $s.Substring(0, [Math]::Min(500, $s.Length))
    $up = $true
    break
  } catch {
    if ($i % 3 -eq 0) { Write-Host ("waiting learner t={0}s ..." -f ($i * 5)) }
  }
}
if (-not $up) { throw 'learner HTTP never came up' }

Write-Host '=== START WH1 ===' -ForegroundColor Green
$wh1Start = @'
$ErrorActionPreference = "Continue"
Set-Location D:\re1_rl
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
$tn = "RE1_almanac_wh1_worker"
$tr = 'D:\re1_rl\fleet\local\run_distributed_worker_workhorse1.cmd'
schtasks /Delete /TN $tn /F 2>$null | Out-Null
schtasks /Create /TN $tn /TR $tr /SC ONCE /ST 00:00 /RL HIGHEST /F | Out-Host
schtasks /Run /TN $tn | Out-Host
Start-Sleep 12
$py = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue | Where-Object { $_.CommandLine -match "distributed_train" })
Write-Output ("WH1_START py={0} head={1}" -f $py.Count, (git rev-parse --short HEAD))
'@
Set-Content (Join-Path $env:TEMP 'wh1_start.ps1') $wh1Start -Encoding UTF8
& scp.exe -o ConnectTimeout=15 (Join-Path $env:TEMP 'wh1_start.ps1') "${WH1}:D:/re1_rl/_tmp_hard_start.ps1"
Invoke-FleetSsh $WH1 'powershell -NoProfile -ExecutionPolicy Bypass -File D:\re1_rl\_tmp_hard_start.ps1'

Write-Host '=== START PKING ===' -ForegroundColor Green
cmd /c 'D:\re1_rl\fleet\local\start_worker_detached_pking.cmd'
Start-Sleep 25

Write-Host '=== FINAL STATUS ===' -ForegroundColor Green
$s = (Invoke-WebRequest -UseBasicParsing http://192.168.0.116:8765/status -TimeoutSec 8).Content
Write-Host $s
Write-Host ("PKING emu={0} train_py={1}" -f @(Get-Process EmuHawk -EA SilentlyContinue).Count, @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue | Where-Object { $_.CommandLine -match 'distributed_train' }).Count)
Write-Host 'HARD_RESTART_DONE'
