param(
  [int]$DelaySec = 0
)
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\sshuser\re1_rl'
Set-Location $repo

if ($DelaySec -gt 0) {
  Write-Output ("delay ${DelaySec}s before WH2 worker start")
  Start-Sleep -Seconds $DelaySec
}

$tn = 'RE1_WH2_PlannerLoyalWorker_Admin'
Unregister-ScheduledTask -TaskName $tn -Confirm:$false -EA SilentlyContinue
$action = New-ScheduledTaskAction `
  -Execute 'cmd.exe' `
  -Argument '/c fleet\local\run_distributed_worker_workhorse2_planner_loyal.cmd' `
  -WorkingDirectory $repo
$principal = New-ScheduledTaskPrincipal -UserId 'admin' -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $tn -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $tn
Write-Output 'WH2_PL_WORKER_SCHEDULED'
