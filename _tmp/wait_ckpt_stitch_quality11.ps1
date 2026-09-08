# Wait for a newer planner_loyal NN checkpoint, then tear down, git sync,
# stitch 11-dim quality on every machine, and restart the fleet.
$ErrorActionPreference = 'Stop'
$WH1 = 'sshuser@192.168.0.203'
$WH2 = 'sshuser@192.168.0.116'
$WH3 = 'sshuser@192.168.0.229'
$ROOT = 'D:\re1_rl'
$BRANCH = 'feature/planner-loyal-ppo'
$TimeoutMinutes = 180
$PollSec = 30
$Log = Join-Path $ROOT '_tmp\wait_ckpt_stitch_quality11.log'
$Stitch = Join-Path $ROOT '_tmp\_stitch_planner_loyal_quality_11.py'
Set-Location $ROOT
Remove-Item -ErrorAction SilentlyContinue $Log

function Write-Log([string]$Msg) {
  $line = ('{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Msg)
  Write-Host $line
  Add-Content -Path $Log -Value $line
}

function Get-LatestCkpt {
  $raw = (& ssh.exe -o ConnectTimeout=15 -o BatchMode=yes $WH3 'type C:\Users\sshuser\re1_rl\data\checkpoints\planner_loyal_shield_key\latest.json' 2>&1) -join "`n"
  $steps = $null; $saved = $null; $path = $null; $sha = $null
  if ($raw -match '"steps"\s*:\s*(\d+)') { $steps = [int64]$Matches[1] }
  if ($raw -match '"saved_at"\s*:\s*"([^"]+)"') { $saved = $Matches[1] }
  if ($raw -match '"path"\s*:\s*"([^"]+)"') { $path = $Matches[1] }
  if ($raw -match '"sha256"\s*:\s*"([^"]+)"') { $sha = $Matches[1] }
  elseif ($raw -match '"digest"\s*:\s*"([^"]+)"') { $sha = $Matches[1] }
  return [pscustomobject]@{ Steps = $steps; SavedAt = $saved; Path = $path; Sha = $sha; Raw = $raw }
}

function Get-Status {
  try {
    return (Invoke-WebRequest -UseBasicParsing http://192.168.0.229:8765/status -TimeoutSec 8).Content | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Invoke-FleetSsh([string]$HostName, [string]$Cmd) {
  Write-Log "ssh $HostName :: $Cmd"
  & ssh.exe -o ConnectTimeout=30 -o BatchMode=yes $HostName $Cmd
  if ($LASTEXITCODE -ne 0) { throw "ssh failed ($LASTEXITCODE): $HostName" }
}

Write-Log ("SHA pking local=" + (git rev-parse --short HEAD))
$base = Get-LatestCkpt
$st0 = Get-Status
$basePolicy = if ($st0) { [int]$st0.policy_version } else { -1 }
if ($null -eq $base.Steps) { throw 'could not read WH3 planner_loyal_shield_key latest.json' }
Write-Log "WAIT baseline steps=$($base.Steps) saved_at=$($base.SavedAt) path=$($base.Path) policy=$basePolicy"

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$new = $null
while ((Get-Date) -lt $deadline) {
  $cur = Get-LatestCkpt
  $st = Get-Status
  $pol = if ($st) { [int]$st.policy_version } else { -1 }
  Write-Log ("poll steps={0} saved_at={1} policy={2}" -f $(if ($cur.Steps) { $cur.Steps } else { '?' }), $(if ($cur.SavedAt) { $cur.SavedAt } else { '?' }), $pol)
  if ($null -ne $cur.Steps -and $cur.Steps -gt $base.Steps) {
    $new = $cur
    break
  }
  if ($pol -gt $basePolicy -and $basePolicy -ge 0) {
    $new = $cur
    break
  }
  Start-Sleep $PollSec
}
if ($null -eq $new) {
  throw "timeout waiting for new checkpoint (baseline steps=$($base.Steps) policy=$basePolicy)"
}
$polNew = Get-Status
Write-Log "NEW_CKPT steps=$($new.Steps) saved_at=$($new.SavedAt) path=$($new.Path) policy=$(if ($polNew) { $polNew.policy_version } else { '?' })"

Write-Log 'clear remote sync blockers (pin file preserved — do not wipe pins)'
# Intentionally do NOT git-checkout planner_loyal_reset_pin.env: pins are local
# runtime config and must survive fleet restart so tips stay weighted/uniform.

Write-Log '=== TEARDOWN ==='
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ROOT '_tmp\_stop_fleet_procs.ps1')
Invoke-FleetSsh $WH2 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul & taskkill /F /IM Resident_Evil_Director_s_Cut_Recompiled.exe 2>nul & exit 0'
Invoke-FleetSsh $WH1 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul & taskkill /F /IM Resident_Evil_Director_s_Cut_Recompiled.exe 2>nul & exit 0'
Invoke-FleetSsh $WH3 'taskkill /F /IM python.exe 2>nul & taskkill /F /IM EmuHawk.exe 2>nul & taskkill /F /IM Resident_Evil_Director_s_Cut_Recompiled.exe 2>nul & exit 0'
Start-Sleep -Seconds 5

Write-Log '=== GIT SYNC ==='
git pull --ff-only origin $BRANCH
Write-Log ("PKING=" + (git rev-parse --short HEAD))
Invoke-FleetSsh $WH2 "cd /d C:\Users\sshuser\re1_rl && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"
Invoke-FleetSsh $WH1 "cd /d D:\re1_rl && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"
Invoke-FleetSsh $WH3 "cd /d C:\Users\sshuser\re1_rl && git pull --ff-only origin $BRANCH && git rev-parse --short HEAD"

$shaP = (git rev-parse --short HEAD).Trim()
$sha1 = (& ssh.exe -o ConnectTimeout=15 -o BatchMode=yes $WH1 'cd /d D:\re1_rl && git rev-parse --short HEAD').Trim()
$sha2 = (& ssh.exe -o ConnectTimeout=15 -o BatchMode=yes $WH2 'cd /d C:\Users\sshuser\re1_rl && git rev-parse --short HEAD').Trim()
$sha3 = (& ssh.exe -o ConnectTimeout=15 -o BatchMode=yes $WH3 'cd /d C:\Users\sshuser\re1_rl && git rev-parse --short HEAD').Trim()
Write-Log "SHA pking=$shaP wh1=$sha1 wh2=$sha2 wh3=$sha3"
if (@($shaP, $sha1, $sha2, $sha3) | Where-Object { $_ -ne $shaP }) {
  throw "SHA mismatch after sync"
}

Write-Log '=== REGEN KILLS + STITCH 11-DIM QUALITY (all machines) ==='
$PyLocal = Join-Path $ROOT 'venv\Scripts\python.exe'
if (-not (Test-Path $PyLocal)) { $PyLocal = 'python' }
& $PyLocal $Stitch
if ($LASTEXITCODE -ne 0) { throw "pking regen+stitch failed ($LASTEXITCODE)" }
Invoke-FleetSsh $WH1 'cd /d D:\re1_rl && set RE1_RL_ROOT=D:\re1_rl&& set RE1_RECOMP_ROOT=D:\re1_recomp&& venv\Scripts\python.exe _tmp\_stitch_planner_loyal_quality_11.py'
Invoke-FleetSsh $WH2 'cd /d C:\Users\sshuser\re1_rl && set RE1_RL_ROOT=C:\Users\sshuser\re1_rl&& set RE1_RECOMP_ROOT=C:\re1_recomp&& venv\Scripts\python.exe _tmp\_stitch_planner_loyal_quality_11.py'
Invoke-FleetSsh $WH3 'cd /d C:\Users\sshuser\re1_rl && set RE1_RL_ROOT=C:\Users\sshuser\re1_rl&& set RE1_RECOMP_ROOT=C:\re1_recomp&& venv\Scripts\python.exe _tmp\_stitch_planner_loyal_quality_11.py'

Write-Log '=== RESTART (skip teardown+sync; already done) ==='
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ROOT 'fleet\local\restart_planner_loyal_fleet.ps1') -SkipTeardown -SkipSync
if ($LASTEXITCODE -ne 0) { throw "fleet restart failed ($LASTEXITCODE)" }

Start-Sleep -Seconds 20
$ckptAfter = Get-LatestCkpt
$stAfter = Get-Status
Write-Log "POST ckpt steps=$($ckptAfter.Steps) saved_at=$($ckptAfter.SavedAt) path=$($ckptAfter.Path)"
if ($stAfter) {
  Write-Log ("POST status policy={0}" -f $stAfter.policy_version)
}
if ($null -eq $ckptAfter.Steps -or $ckptAfter.Steps -lt $new.Steps) {
  throw "POST ckpt steps $($ckptAfter.Steps) older than waited $($new.Steps)"
}
Write-Log 'DONE wait+teardown+sync+stitch+restart'
