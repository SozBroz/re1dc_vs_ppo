$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\sshuser\re1_rl'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
  Where-Object { $_.CommandLine -match 'distributed_train' -and $_.CommandLine -match 'workhorse2' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Get-Process EmuHawk -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Start-Sleep -Seconds 3
$tn = 'RE1_WH2_PlannerLoyalWorker'
$launcher = Join-Path $repo 'fleet\local\run_distributed_worker_workhorse2_planner_loyal.cmd'
Unregister-ScheduledTask -TaskName $tn -Confirm:$false -EA SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c fleet\local\start_worker_detached_workhorse2_planner_loyal.cmd' -WorkingDirectory $repo
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $tn -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $tn
Write-Output 'wh2 worker restarted'
