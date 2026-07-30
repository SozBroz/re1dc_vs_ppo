# Purge Go-Explore cell debris on pking + fleet workers (remote python for reliability).
$ErrorActionPreference = 'Stop'
$WH1 = 'sshuser@192.168.0.203'
$WH2 = 'sshuser@192.168.0.116'
$ROOT = 'D:\re1_rl'

function Invoke-FleetSsh([string]$HostAlias, [string]$Cmd) {
  Write-Host ">>> $HostAlias" -ForegroundColor DarkCyan
  ssh -o BatchMode=yes -o ConnectTimeout=15 $HostAlias $Cmd
  if ($LASTEXITCODE -ne 0) { throw "ssh failed ($LASTEXITCODE): $HostAlias" }
}

function Invoke-RemotePurge([string]$HostAlias, [string]$RepoCmd) {
  Invoke-FleetSsh $HostAlias "cd /d $RepoCmd && venv\Scripts\python.exe scripts\purge_go_explore_orphans.py --also-lua --nuke-all"
}

Write-Host '=== PKING local cleanup ===' -ForegroundColor Cyan
Set-Location $ROOT
python scripts\purge_go_explore_orphans.py --also-lua --nuke-all

Write-Host '=== WH1 cleanup ===' -ForegroundColor Cyan
Invoke-RemotePurge $WH1 'D:\re1_rl'

Write-Host '=== WH2 cleanup ===' -ForegroundColor Cyan
Invoke-RemotePurge $WH2 'C:\Users\sshuser\re1_rl'

Write-Host '=== VERIFY (remote snapshot) ===' -ForegroundColor Green
$Snap = Join-Path $ROOT '_tmp_disk_snapshot.py'
if (Test-Path $Snap) {
  scp -o ConnectTimeout=15 $Snap "${WH1}:D:/re1_rl/_tmp_disk_snapshot.py" | Out-Null
  scp -o ConnectTimeout=15 $Snap "${WH2}:C:/Users/sshuser/re1_rl/_tmp_disk_snapshot.py" | Out-Null
  Invoke-FleetSsh $WH1 'cd /d D:\re1_rl && venv\Scripts\python.exe _tmp_disk_snapshot.py'
  Invoke-FleetSsh $WH2 'cd /d C:\Users\sshuser\re1_rl && venv\Scripts\python.exe _tmp_disk_snapshot.py'
}

Write-Host '=== PKING D: ===' -ForegroundColor Green
Get-PSDrive D | Select-Object @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}, @{n='UsedGB';e={[math]::Round($_.Used/1GB,1)}}
