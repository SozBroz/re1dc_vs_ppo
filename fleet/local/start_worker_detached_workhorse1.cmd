@echo off

REM Detached WH1 worker (192.168.0.203) — needs interactive desktop for EmuHawk.

setlocal

cd /d D:\re1_rl

if not exist data\logs mkdir data\logs



taskkill /F /IM EmuHawk.exe >nul 2>&1

for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe' and CommandLine like '%%distributed_train%%'" get ProcessId /format:csv ^| findstr /r "[0-9]"') do taskkill /F /PID %%P >nul 2>&1

timeout /t 2 /nobreak >nul

REM Fresh heuristics log for this batch (truncate; do not delete).
call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_workhorse1.log"

start "WH1-worker" /MIN cmd /c "cd /d D:\re1_rl && fleet\local\run_distributed_worker_workhorse1.cmd >> data\logs\worker_workhorse1.log 2>&1"

echo Started WH1 worker detached. Tail: type data\logs\worker_workhorse1.log


