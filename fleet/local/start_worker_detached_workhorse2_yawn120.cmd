@echo off
REM Restart WH2 local yawn120 worker only (ports 5555-5582). Do NOT kill the learner.
setlocal
cd /d C:\Users\sshuser\re1_rl

if not exist data\logs mkdir data\logs

powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -EA SilentlyContinue | Where-Object { $_.CommandLine -match 'distributed_train' -and $_.CommandLine -match '--role worker' -and ($_.CommandLine -match 'workhorse2' -or $_.CommandLine -match 'base-port 5555') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }; 5555..5582 | ForEach-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue } }"

timeout /t 3 /nobreak >nul

call "%~dp0flush_log.cmd" "C:\Users\sshuser\re1_rl\data\logs\worker_wh2_yawn120.log"

start "wh2-yawn120" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_worker_workhorse2_yawn120.cmd"

echo Started workhorse2-yawn120. Tail: type data\logs\worker_wh2_yawn120.log
