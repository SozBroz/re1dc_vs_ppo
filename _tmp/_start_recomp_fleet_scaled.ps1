param(
  [switch]$PkingOnly
)

# Start C-RE1 fleet at full BizHawk scale: pking20 / wh1 8 / wh2 28 / wh3 24.
# Remotes start in parallel. Pking is VISIBLE and must not batch-spawn all 20
# at once (READY attach timeouts → crash-loop / "sperging").
$ErrorActionPreference = 'Stop'
$ROOT = 'D:\re1_rl'
$RECOMP = 'D:\re1_recomp'
$WH1 = 'sshuser@192.168.0.203'
$WH2 = 'sshuser@192.168.0.116'
$WH3 = 'sshuser@192.168.0.229'
$LEARNER = 'http://192.168.0.229:8765'

# Visible pking: small batches + stagger. Headless remotes: modest batch.
$PkingBatch = 4
$PkingStaggerS = '0.75'
$RemoteBatchMax = 4
$RemoteStaggerS = '0.5'
$PkingReadyTimeoutSec = 240

if ($PkingOnly) {
  Write-Host '=== START PKING ONLY (staggered visible boot) ===' -ForegroundColor Green
} else {
  Write-Host '=== START C-RE1 WORKERS (20/8/28/24) ===' -ForegroundColor Green
}

function Stop-PkingRecomp {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        $_.CommandLine -match 'pking-recomp' -or
        ($_.CommandLine -match 'distributed_train_parallel' -and $_.CommandLine -match 'pking' -and $_.CommandLine -match 'worker' -and $_.CommandLine -notmatch 'learner')
      )
    } |
    ForEach-Object {
      Write-Host "kill pking py PID=$($_.ProcessId)"
      Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue
    }
  Get-Process Resident_Evil_Director_s_Cut_Recompiled -EA SilentlyContinue |
    ForEach-Object {
      Write-Host "kill pking recomp PID=$($_.Id)"
      Stop-Process -Id $_.Id -Force -EA SilentlyContinue
    }
  Start-Sleep -Seconds 2
}

function Wait-PkingOnLearner([int]$TimeoutSec) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  $needEnvs = [int]$env:N_ENVS
  if ($needEnvs -le 0) { $needEnvs = 20 }
  $needCre1 = [Math]::Max(1, $needEnvs - 2)
  $goodPolls = 0
  Write-Host "Waiting for pking-recomp healthy (n_envs>=$needEnvs cre1>=$needCre1, up to ${TimeoutSec}s)..." -ForegroundColor Yellow
  while ((Get-Date) -lt $deadline) {
    $cre1 = @(Get-Process Resident_Evil_Director_s_Cut_Recompiled -EA SilentlyContinue).Count
    $log = Join-Path $ROOT 'data\logs\worker_pking-recomp.log'
    $restart = 0
    $spawnFail = 0
    if (Test-Path $log) {
      $restart = @(Select-String -Path $log -Pattern 'async worker restart attempt' -EA SilentlyContinue).Count
      $spawnFail = @(Select-String -Path $log -Pattern 'spawn failed' -EA SilentlyContinue).Count
    }
    if ($restart -ge 1 -or $spawnFail -ge 1) {
      throw "pking boot unhealthy (restart=$restart spawn_fail=$spawnFail); aborting start"
    }
    $n = 0
    try {
      $st = (Invoke-WebRequest -UseBasicParsing "$LEARNER/status" -TimeoutSec 5).Content | ConvertFrom-Json
      $w = $st.workers.'pking-recomp'
      if ($null -ne $w) { $n = [int]$w.n_envs }
    } catch {
      # learner / network blip
    }
    Write-Host ("  … n_envs={0} cre1={1}" -f $n, $cre1)
    if ($n -ge $needEnvs -and $cre1 -ge $needCre1) {
      $goodPolls++
      if ($goodPolls -ge 2) {
        Write-Host ("pking-recomp healthy n_envs={0} cre1={1}" -f $n, $cre1) -ForegroundColor Green
        return
      }
    } else {
      $goodPolls = 0
    }
    Start-Sleep -Seconds 5
  }
  throw "pking-recomp not healthy within ${TimeoutSec}s"
}

Stop-PkingRecomp

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
$env:RE1_ACTOR_STARTUP_BATCH_SIZE = "$PkingBatch"
$env:RE1_ACTOR_STARTUP_STAGGER_S_PER_RANK = $PkingStaggerS
$env:RE1_GRID_LOCK_INTERVAL_S = '0.15'
$env:RE1_GRID_MONITOR = 'right'
$env:RE1_PLANNER_RESET_PIN_FILE = 'D:\re1_rl\data\planner_loyal_reset_pin.env'
$env:RE1_PLANNER_HOP_SCORE_V1 = '1'
New-Item -ItemType Directory -Force -Path (Join-Path $ROOT 'data\logs') | Out-Null
# Rotate prior log so crash-loop detection is for this boot only.
$logPath = Join-Path $ROOT 'data\logs\worker_pking-recomp.log'
if (Test-Path $logPath) {
  $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
  Move-Item -Force $logPath (Join-Path $ROOT "data\logs\worker_pking-recomp.$stamp.bak.log") -EA SilentlyContinue
}
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 0 /f >$null 2>&1
reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f >$null 2>&1
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$RECOMP\ecosystem\run_recomp_worker.cmd`"" -WorkingDirectory $ROOT -WindowStyle Minimized
Write-Host "pking-recomp start issued (visible 20, batch=$PkingBatch stagger=${PkingStaggerS}s)"

if (-not $PkingOnly) {
  $wmiPath = Join-Path $ROOT '_tmp\_start_remote_recomp_wmi.ps1'
  $wmi = Get-Content $wmiPath -Raw
  if ($wmi -notmatch 'RE1_ECOSYSTEM_ENEMY_WORK') {
    $wmi = $wmi -replace "set RE1_RECOMP_VISIBLE=\`$Visible", "set RE1_RECOMP_VISIBLE=`$Visible`r`nset RE1_ECOSYSTEM_ENEMY_WORK=1"
    Set-Content $wmiPath $wmi -Encoding ASCII
  }

  $remotes = @(
    @{ Ssh=$WH1; Rl='D:\re1_rl'; Recomp='D:\re1_recomp'; Wid='wh1-recomp'; Machine='workhorse1'; NEnvs='8'; BasePort='6600'; Ranks='0-7' },
    @{ Ssh=$WH2; Rl='C:\Users\sshuser\re1_rl'; Recomp='C:\re1_recomp'; Wid='wh2-recomp'; Machine='workhorse2'; NEnvs='28'; BasePort='6700'; Ranks='0-27' },
    @{ Ssh=$WH3; Rl='C:\Users\sshuser\re1_rl'; Recomp='C:\re1_recomp'; Wid='wh3-recomp'; Machine='workhorse3'; NEnvs='24'; BasePort='6800'; Ranks='0-23' }
  )

  $scpJobs = foreach ($r in $remotes) {
    $dest = "$($r.Ssh):$(($r.Rl -replace '\\','/'))/_tmp/_start_remote_recomp_wmi.ps1"
    Start-Job -ScriptBlock {
      param($Src, $Dest, $Ssh)
      & scp.exe -o ConnectTimeout=20 $Src $Dest
      if ($LASTEXITCODE -ne 0) { throw "scp failed $Ssh ($LASTEXITCODE)" }
    } -ArgumentList $wmiPath, $dest, $r.Ssh
  }
  $scpJobs | Wait-Job | Out-Null
  foreach ($j in $scpJobs) {
    if ($j.State -ne 'Completed') {
      Receive-Job $j
      throw "remote scp job failed: $($j.State)"
    }
    Remove-Job $j -Force
  }

  $startJobs = foreach ($r in $remotes) {
    Start-Job -ScriptBlock {
      param($Ssh, $Rl, $Recomp, $Wid, $Machine, $NEnvs, $BasePort, $Ranks, $BatchMax, $Stagger)
      $batch = [Math]::Min([int]$NEnvs, [int]$BatchMax)
      $argLine = "-Rl `"$Rl`" -Recomp `"$Recomp`" -WorkerId $Wid -MachineName $Machine -NEnvs $NEnvs -BasePort $BasePort -ActorRanks $Ranks -Visible 0 -StartupBatch $batch -StartupStaggerS $Stagger"
      $out = & ssh.exe -o ConnectTimeout=90 $Ssh "powershell -NoProfile -ExecutionPolicy Bypass -File `"$Rl\_tmp\_start_remote_recomp_wmi.ps1`" $argLine" 2>&1
      $code = $LASTEXITCODE
      [pscustomobject]@{
        Wid = $Wid
        ExitCode = $code
        Out = ($out | Out-String)
      }
    } -ArgumentList $r.Ssh, $r.Rl, $r.Recomp, $r.Wid, $r.Machine, $r.NEnvs, $r.BasePort, $r.Ranks, $RemoteBatchMax, $RemoteStaggerS
  }
  $startJobs | Wait-Job | Out-Null
  $failed = $false
  foreach ($j in $startJobs) {
    $recv = Receive-Job $j
    if ($j.State -ne 'Completed' -or $recv.ExitCode -ne 0) {
      Write-Host ("FAIL {0}: {1}" -f $recv.Wid, $recv.Out)
      $failed = $true
    } else {
      $tail = (($recv.Out -split "`n") | Where-Object { $_.Trim() } | Select-Object -Last 3) -join ' | '
      Write-Host ("OK {0}: {1}" -f $recv.Wid, $tail)
    }
    Remove-Job $j -Force
  }
  if ($failed) { throw 'one or more remote starts failed' }
}

Wait-PkingOnLearner -TimeoutSec $PkingReadyTimeoutSec
Write-Host 'RECOMP_FLEET_START_OK (pking gated)' -ForegroundColor Green
