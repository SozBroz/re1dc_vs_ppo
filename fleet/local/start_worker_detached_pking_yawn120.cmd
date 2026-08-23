@echo off
REM Restart pking yawn120 grind worker only (ports 5855-5862).
setlocal
cd /d D:\re1_rl

if not exist data\logs mkdir data\logs

for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe' and CommandLine like '%%distributed_train%%' and (CommandLine like '%%worker-id pking-yawn120%%' or CommandLine like '%%base-port 5855%%')" get ProcessId /format:csv ^| findstr /r "[0-9]"') do taskkill /F /PID %%P >nul 2>&1

powershell -NoProfile -Command ^
  "$ports = 5855..5862; Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

timeout /t 2 /nobreak >nul

call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_pking_yawn120.log"

start "pking-yawn120" /MIN cmd /c "cd /d D:\re1_rl && fleet\local\run_distributed_worker_pking_yawn120.cmd"

echo Started pking-yawn120 worker. Tail: type data\logs\worker_pking_yawn120.log
