param(
  [Parameter(Mandatory = $true)][ValidateSet('wh2', 'wh1')][string]$Role
)
$ErrorActionPreference = 'Continue'
if ($Role -eq 'wh2') {
  $repo = 'C:\Users\sshuser\re1_rl'
  $task = 'RE1_almanac_learner'
  $ports = @(8765) + @(5555..5590)
} else {
  $repo = 'D:\re1_rl'
  $task = 'RE1_almanac_wh1_worker'
  $ports = @(5655..5670)
}
Set-Location $repo
schtasks /End /TN $task 2>$null | Out-Null
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
  Where-Object { $_.CommandLine -match 'distributed_train' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Stop-Process -Name EmuHawk -Force -EA SilentlyContinue
Start-Sleep 3
foreach ($p in $ports) {
  Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }
}
# Drop PB sync locks so a crashed mid-copy cannot wedge the next run.
$pbChamps = Join-Path $repo 'states\pb\champions'
$locksCleared = 0
if (Test-Path $pbChamps) {
  Get-ChildItem $pbChamps -Directory -EA SilentlyContinue | ForEach-Object {
    $lp = Join-Path $_.FullName 'champion.sync.lock'
    if (Test-Path $lp) { Remove-Item -Force $lp -EA SilentlyContinue; $locksCleared++ }
    $incoming = Join-Path $_.FullName '.incoming'
    if (Test-Path $incoming) { Remove-Item -Recurse -Force $incoming -EA SilentlyContinue }
  }
}
Start-Sleep 2
$emu = @(Get-Process EmuHawk -EA SilentlyContinue).Count
$py = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
  Where-Object { $_.CommandLine -match 'distributed_train' }).Count
Write-Output ("{0}_DOWN emu={1} py={2} pb_locks_cleared={3} head={4}" -f $Role.ToUpper(), $emu, $py, $locksCleared, (git rev-parse --short HEAD))
