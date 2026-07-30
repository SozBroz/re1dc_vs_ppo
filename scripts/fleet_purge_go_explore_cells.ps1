# Purge Go-Explore cell debris on pking + fleet workers.
$ErrorActionPreference = 'Stop'
$WH1 = 'sshuser@192.168.0.203'
$WH2 = 'sshuser@192.168.0.116'
$ROOT = 'D:\re1_rl'

function Invoke-FleetSsh([string]$HostAlias, [string]$Cmd) {
  ssh -o BatchMode=yes -o ConnectTimeout=15 $HostAlias $Cmd
}

Write-Host '=== PKING local cleanup ===' -ForegroundColor Cyan
Set-Location $ROOT
python scripts\purge_go_explore_orphans.py --also-lua --nuke-all

Write-Host '=== WH1 (203) cleanup ===' -ForegroundColor Cyan
Invoke-FleetSsh $WH1 'if exist D:\re1_rl\lua\data\go_explore\cells rmdir /s /q D:\re1_rl\lua\data\go_explore\cells & if exist D:\re1_rl\data\go_explore\cells rmdir /s /q D:\re1_rl\data\go_explore\cells & mkdir D:\re1_rl\data\go_explore\cells 2>nul & echo WH1_DONE'

Write-Host '=== WH2 (116) cleanup ===' -ForegroundColor Cyan
Invoke-FleetSsh $WH2 'if exist C:\Users\sshuser\re1_rl\lua\data\go_explore\cells rmdir /s /q C:\Users\sshuser\re1_rl\lua\data\go_explore\cells & if exist C:\Users\sshuser\re1_rl\data\go_explore\cells rmdir /s /q C:\Users\sshuser\re1_rl\data\go_explore\cells & mkdir C:\Users\sshuser\re1_rl\data\go_explore\cells 2>nul & echo WH2_DONE'

Write-Host '=== DISK AFTER ===' -ForegroundColor Green
Get-PSDrive D | Select-Object Used, Free
Invoke-FleetSsh $WH1 'wmic logicaldisk where DeviceID="D:" get FreeSpace,Size /format:list 2>nul || echo WH1_NO_D'
Invoke-FleetSsh $WH2 'wmic logicaldisk where DeviceID="C:" get FreeSpace,Size /format:list 2>nul'
