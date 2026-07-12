# Launch pking worker in the local interactive desktop.
param(
    [string]$RepoRoot = 'D:\re1_rl',
    [string]$TaskName = 'RE1_worker_pking'
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

$wrapper = Join-Path $RepoRoot 'fleet\local\run_distributed_worker_pking.cmd'
$launcher = Join-Path $RepoRoot 'fleet\local\_pking_worker_launcher.cmd'
@"
@echo off
cd /d $RepoRoot
call "$wrapper" >> data\logs\worker_pking.log 2>&1
"@ | Set-Content -Path $launcher -Encoding ASCII

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$launcher`"" -WorkingDirectory $RepoRoot
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "STARTED $TaskName -> $wrapper"
