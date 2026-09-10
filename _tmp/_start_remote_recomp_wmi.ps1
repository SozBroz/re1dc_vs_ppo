# Start remote C-RE1 worker via WMI (survives SSH; no Interactive session required).
param(
  [Parameter(Mandatory=$true)][string]$Rl,
  [Parameter(Mandatory=$true)][string]$Recomp,
  [Parameter(Mandatory=$true)][string]$WorkerId,
  [Parameter(Mandatory=$true)][string]$MachineName,
  [Parameter(Mandatory=$true)][string]$NEnvs,
  [Parameter(Mandatory=$true)][string]$BasePort,
  [Parameter(Mandatory=$true)][string]$ActorRanks,
  [string]$Visible = '0',
  [int]$StartupBatch = 0,
  [string]$StartupStaggerS = '0.25'
)
$ErrorActionPreference = 'Stop'
$launcher = Join-Path $Rl "_tmp\launch_$WorkerId.cmd"
$logDir = Join-Path $Rl 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir,(Join-Path $Rl '_tmp') | Out-Null

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
  Where-Object {
    $_.CommandLine -match [regex]::Escape($WorkerId) -or
    ($_.CommandLine -match 'distributed_train' -and $_.CommandLine -match 'worker' -and $_.CommandLine -match $MachineName -and $_.CommandLine -notmatch 'learner')
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Get-Process -Name 'Resident_Evil_Director_s_Cut_Recompiled' -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Start-Sleep -Seconds 2

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = Join-Path $logDir "worker_${WorkerId}.$stamp.log"
$batchSize = if ($StartupBatch -gt 0) { [int]$StartupBatch } else { [Math]::Min([int]$NEnvs, 8) }
if ($batchSize -lt 1) { $batchSize = 1 }

$cmdBody = @"
@echo off
set RE1_RL_ROOT=$Rl
set RE1_RECOMP_ROOT=$Recomp
set RE1_RECOMP_VISIBLE=$Visible
set RE1_ECOSYSTEM_ENEMY_WORK=1
set RE1_ACTOR_STARTUP_BATCH_SIZE=$batchSize
set RE1_ACTOR_STARTUP_STAGGER_S_PER_RANK=$StartupStaggerS
set RE1_GRID_LOCK_INTERVAL_S=0.15
set RE1_GRID_MONITOR=right
set LEARNER_HOST=192.168.0.229
set LEARNER_PORT=8765
set RE1_PLANNER_CHUNK=$Rl\data\planner_chunks\cp05_shield_key.json
set RE1_PLANNER_RESET_PIN_FILE=$Rl\data\planner_loyal_reset_pin.env
set RE1_PLANNER_HOP_SCORE_V1=1
set N_ENVS=$NEnvs
set BASE_PORT=$BasePort
set WORKER_ID=$WorkerId
set MACHINE_NAME=$MachineName
set ACTOR_RANKS=$ActorRanks
set RE1_WORKER_LOG=$log
set GRID_COLS=8
set GRID_ROWS=4
cd /d $Rl
call $Recomp\ecosystem\run_recomp_worker.cmd
"@
if ([int]$NEnvs -le 20) {
  $cmdBody = $cmdBody -replace 'set GRID_COLS=8\r?\nset GRID_ROWS=4\r?\n', "set GRID_COLS=5`r`nset GRID_ROWS=4`r`n"
}
Set-Content -Path $launcher -Value $cmdBody -Encoding ASCII

$wmiCmd = "cmd.exe /c `"$launcher`""
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = $wmiCmd
  CurrentDirectory = $Rl
}
Write-Output ("WMI ReturnValue={0} ProcessId={1}" -f $result.ReturnValue, $result.ProcessId)
if ($result.ReturnValue -ne 0) { exit 1 }
Start-Sleep -Seconds 4
Write-Output ("log_bytes=" + $(if (Test-Path $log) { (Get-Item $log).Length } else { -1 }))
Write-Output "STARTED"
