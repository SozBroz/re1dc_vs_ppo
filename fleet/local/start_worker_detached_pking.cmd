@echo off
REM Restart pking worker only. Do NOT taskkill /IM EmuHawk.exe (kills play_human).
setlocal
cd /d D:\re1_rl

if not exist data\logs mkdir data\logs

REM Kill pking distributed_train only (CommandLine carries --machine-name pking / base-port 5755).
for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe' and CommandLine like '%%distributed_train%%' and (CommandLine like '%%machine-name pking%%' or CommandLine like '%%base-port 5755%%')" get ProcessId /format:csv ^| findstr /r "[0-9]"') do taskkill /F /PID %%P >nul 2>&1

REM Free pking env ports 5755-5774 (EmuHawk listeners for this worker only).
powershell -NoProfile -Command ^
  "$ports = 5755..5774; Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

timeout /t 2 /nobreak >nul

REM Fresh heuristics log for this batch (truncate; do not delete).
call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_pking.log"

start "pking-worker" /MIN cmd /c "cd /d D:\re1_rl && fleet\local\run_distributed_worker_pking.cmd >> data\logs\worker_pking.log 2>&1"

echo Started pking worker detached. Tail: type data\logs\worker_pking.log
