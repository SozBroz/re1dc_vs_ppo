param(
  [int]$DelaySec = 0
)
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\sshuser\re1_rl'
Set-Location $repo

if ($DelaySec -gt 0) {
  Write-Output ("delay ${DelaySec}s before WH3 worker start")
  Start-Sleep -Seconds $DelaySec
}

# Console session on WH3 is `admin`, not sshuser. EmuHawk must start in that desktop.
$tn = 'RE1_WH3_PlannerLoyalWorker_Admin'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue |
  Where-Object { $_.CommandLine -match 'workhorse3-planner-loyal' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Unregister-ScheduledTask -TaskName $tn -Confirm:$false -EA SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c fleet\local\start_worker_detached_workhorse3_planner_loyal.cmd' -WorkingDirectory $repo
$principal = New-ScheduledTaskPrincipal -UserId 'admin' -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $tn -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $tn
Write-Output 'WH3_PL_WORKER_ADMIN_SCHEDULED'
