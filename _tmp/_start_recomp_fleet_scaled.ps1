# Start C-RE1 fleet at full BizHawk scale: pking20 / wh1 8 / wh2 28 / wh3 24.
$ErrorActionPreference = 'Stop'
$ROOT = 'D:\re1_rl'
$RECOMP = 'D:\re1_recomp'
$WH1 = 'sshuser@192.168.0.203'
$WH2 = 'sshuser@192.168.0.116'
$WH3 = 'sshuser@192.168.0.229'

Write-Host '=== START C-RE1 WORKERS (20/8/28/24) ===' -ForegroundColor Green
$env:RE1_RL_ROOT = $ROOT
$env:RE1_RECOMP_ROOT = $RECOMP
$env:RE1_RECOMP_VISIBLE = '1'
$env:N_ENVS = '20'
$env:BASE_PORT = '6500'
$env:WORKER_ID = 'pking-recomp'
$env:MACHINE_NAME = 'pking'
$env:ACTOR_RANKS = '0-19'
$env:GRID_COLS = '5'
$env:GRID_ROWS = '4'
$env:RE1_ECOSYSTEM_ENEMY_WORK = '1'
$env:RE1_ACTOR_STARTUP_BATCH_SIZE = $env:N_ENVS
$env:RE1_ACTOR_STARTUP_STAGGER_S_PER_RANK = '0'
$env:RE1_GRID_LOCK_INTERVAL_S = '0.15'
$env:RE1_GRID_MONITOR = 'right'
$env:RE1_PLANNER_RESET_PIN_FILE = 'D:\re1_rl\data\planner_loyal_reset_pin.env'
$env:RE1_PLANNER_HOP_SCORE_V1 = '1'
New-Item -ItemType Directory -Force -Path (Join-Path $ROOT 'data\logs') | Out-Null
Remove-Item -Force (Join-Path $ROOT 'data\logs\worker_pking-recomp.log') -EA SilentlyContinue
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 0 /f >$null 2>&1
reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f >$null 2>&1
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$RECOMP\ecosystem\run_recomp_worker.cmd`"" -WorkingDirectory $ROOT -WindowStyle Minimized
Write-Host 'pking-recomp start issued (visible 20)'

function Start-RemoteRecomp([string]$Ssh, [string]$Rl, [string]$Recomp, [string]$Wid, [string]$Machine, [string]$NEnvs, [string]$BasePort, [string]$Ranks) {
  scp -o ConnectTimeout=20 (Join-Path $ROOT '_tmp\_start_remote_recomp_wmi.ps1') "${Ssh}:$(($Rl -replace '\\','/'))/_tmp/_start_remote_recomp_wmi.ps1"
  # refresh launcher on remote to set ENEMY_WORK
  $args = "-Rl `"$Rl`" -Recomp `"$Recomp`" -WorkerId $Wid -MachineName $Machine -NEnvs $NEnvs -BasePort $BasePort -ActorRanks $Ranks -Visible 0"
  ssh -o ConnectTimeout=90 $Ssh "powershell -NoProfile -ExecutionPolicy Bypass -File `"$Rl\_tmp\_start_remote_recomp_wmi.ps1`" $args"
}

# Patch remote WMI starter env to include ENEMY_WORK=1 (edit local copy used below)
$wmi = Get-Content (Join-Path $ROOT '_tmp\_start_remote_recomp_wmi.ps1') -Raw
if ($wmi -notmatch 'RE1_ECOSYSTEM_ENEMY_WORK') {
  $wmi = $wmi -replace "set RE1_RECOMP_VISIBLE=\`$Visible", "set RE1_RECOMP_VISIBLE=`$Visible`r`nset RE1_ECOSYSTEM_ENEMY_WORK=1"
  Set-Content (Join-Path $ROOT '_tmp\_start_remote_recomp_wmi.ps1') $wmi -Encoding ASCII
}

Start-RemoteRecomp $WH1 'D:\re1_rl' 'D:\re1_recomp' 'wh1-recomp' 'workhorse1' '8' '6600' '0-7'
Start-RemoteRecomp $WH2 'C:\Users\sshuser\re1_rl' 'C:\re1_recomp' 'wh2-recomp' 'workhorse2' '28' '6700' '0-27'
Start-RemoteRecomp $WH3 'C:\Users\sshuser\re1_rl' 'C:\re1_recomp' 'wh3-recomp' 'workhorse3' '24' '6800' '0-23'
Write-Host 'RECOMP_FLEET_START_ISSUED' -ForegroundColor Green
