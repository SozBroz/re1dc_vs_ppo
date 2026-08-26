$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\sshuser\re1_rl'
Set-Location $repo

Write-Output 'Stopping WH2 planner-loyal worker...'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
  Where-Object { $_.CommandLine -match 'distributed_train' -and $_.CommandLine -match 'workhorse2-planner-loyal' } |
  ForEach-Object {
    & taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null
  }

Write-Output 'Killing EmuHawks and clearing port claims...'
Get-Process EmuHawk -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
$map = Join-Path $repo 'data\emu_port_by_pid'
if (Test-Path $map) {
  Get-ChildItem $map -File -EA SilentlyContinue | Remove-Item -Force -EA SilentlyContinue
}

for ($i = 0; $i -lt 12; $i++) {
  $n = @(Get-Process EmuHawk -EA SilentlyContinue).Count
  if ($n -eq 0) { break }
  Start-Sleep -Seconds 2
}
Write-Output ("emu_remaining=" + @(Get-Process EmuHawk -EA SilentlyContinue).Count)

$tn = 'RE1_WH2_PlannerLoyalWorker'
$launcher = Join-Path $repo 'fleet\local\run_distributed_worker_workhorse2_planner_loyal.cmd'
schtasks /Delete /TN $tn /F 2>$null | Out-Null
schtasks /Create /TN $tn /TR $launcher /SC ONCE /ST 00:00 /RL HIGHEST /F | Out-Host
schtasks /Run /TN $tn | Out-Host
Write-Output 'WH2_PL_WORKER_SCHTASKS_NOIT'
