$ErrorActionPreference = 'SilentlyContinue'
Set-Location 'C:\Users\sshuser\re1_rl'
New-Item -ItemType Directory -Force -Path 'data\logs' | Out-Null

Write-Host 'Stopping EmuHawk + distributed_train python...'
Stop-Process -Name EmuHawk -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'distributed_train' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Sleep -Seconds 3

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"[$stamp] restart_learner_wh2_25.ps1 launching learner" |
    Out-File -FilePath 'data\logs\learner_wh2_25.log' -Append -Encoding utf8

# Must run from an interactive desktop session (RDP/console) — not schtasks /Run over SSH.
Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/c', 'fleet\local\run_distributed_learner_wh2_25.cmd' `
    -WorkingDirectory 'C:\Users\sshuser\re1_rl' `
    -WindowStyle Minimized

Write-Host 'WH2 learner launched. Resume uses newest convention checkpoint by mtime (--resume auto).'
Write-Host 'Tail: Get-Content data\logs\learner_wh2_25.log -Tail 30 -Wait'
