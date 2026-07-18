$ErrorActionPreference = 'SilentlyContinue'
Set-Location 'D:\re1_rl'
New-Item -ItemType Directory -Force -Path 'data\logs' | Out-Null
Stop-Process -Name EmuHawk -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'distributed_train' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 2
# Fresh heuristics log for this batch (truncate; do not delete).
& 'D:\re1_rl\fleet\local\flush_log.cmd' 'D:\re1_rl\data\logs\worker_pking.log'
Start-Process cmd.exe `
    -ArgumentList '/c', 'fleet\local\run_distributed_worker_pking.cmd >> data\logs\worker_pking.log 2>&1' `
    -WorkingDirectory 'D:\re1_rl' `
    -WindowStyle Minimized
Write-Host 'pking worker started'
