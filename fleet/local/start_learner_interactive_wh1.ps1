# Launch WH1 learner in sshuser's interactive desktop.
param(
    [string]$RepoRoot = 'D:\re1_rl',
    [string]$TaskName = 'RE1_learner_wh1'
)
$ErrorActionPreference = 'Continue'
Set-Location $RepoRoot
New-Item -ItemType Directory -Force -Path 'data\logs' | Out-Null

Write-Host 'Stopping stale EmuHawk / distributed_train...'
Stop-Process -Name EmuHawk -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'distributed_train' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$cmd = Join-Path $RepoRoot 'fleet\local\run_distributed_learner_wh1.cmd'
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$cmd`"" -WorkingDirectory $RepoRoot
$principal = New-ScheduledTaskPrincipal -UserId 'sshuser' -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "STARTED $TaskName -> $cmd"
