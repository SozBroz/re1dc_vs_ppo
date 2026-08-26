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

Start-Sleep -Seconds 2
$cmd = 'cmd.exe /c "' + $repo + '\fleet\local\start_worker_detached_workhorse2_planner_loyal.cmd"'
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine=$cmd; CurrentDirectory=$repo }
Write-Output ("WMI rv={0} pid={1}" -f $r.ReturnValue, $r.ProcessId)
