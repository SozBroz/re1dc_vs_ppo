@echo off
REM Restart WH1 yawn120 grind worker (ports 5655-5662).
setlocal
cd /d D:\re1_rl

if not exist data\logs mkdir data\logs

powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -EA SilentlyContinue | Where-Object { $_.CommandLine -match 'distributed_train' -and ($_.CommandLine -match 'workhorse1' -or $_.CommandLine -match 'base-port 5655') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }; 5655..5662 | ForEach-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue } }"

timeout /t 2 /nobreak >nul

call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_workhorse1_yawn120.log"

start "wh1-yawn120" /MIN cmd /c "cd /d D:\re1_rl && fleet\local\run_distributed_worker_workhorse1_yawn120.cmd"

echo Started workhorse1-yawn120. Tail: type data\logs\worker_workhorse1_yawn120.log
