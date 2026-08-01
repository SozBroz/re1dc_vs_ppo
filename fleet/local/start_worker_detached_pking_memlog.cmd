@echo off
REM Start only the independent pking memlog agent (port 5759, top-right tile).
REM Does NOT launch the regular pking training worker.

setlocal
cd /d D:\re1_rl

if not exist data\logs mkdir data\logs

for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe' and CommandLine like '%%worker-id pking-memlog%%'" get ProcessId /format:csv 2^>nul ^| findstr /r "[0-9]"') do (
  echo pking-memlog already running pid=%%P
  exit /b 0
)

powershell -NoProfile -Command ^
  "$ports = 5759..5759; Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_memlog.log"

start "pking-memlog" cmd /c "cd /d D:\re1_rl && fleet\local\run_memlog_agent.cmd >> data\logs\worker_memlog.log 2>&1"

echo Started pking memlog agent only. Tail: type data\logs\worker_memlog.log
