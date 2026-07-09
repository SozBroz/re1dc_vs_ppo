@echo off
REM Detached WH1 worker for interactive desktop session (RDP/console).
REM Do NOT launch this via bare SSH — BizHawk needs an interactive Windows session.
setlocal
cd /d D:\re1_rl
if not exist data\logs mkdir data\logs

taskkill /F /IM EmuHawk.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

set MACHINE_NAME=workhorse1
set LEARNER_HOST=192.168.0.111
set BASE_PORT=5655
set N_ENVS=8
set SYNC_INTERVAL_S=360

start "WH1-worker" /MIN cmd /c "cd /d D:\re1_rl && set MACHINE_NAME=workhorse1&& set LEARNER_HOST=192.168.0.111&& set BASE_PORT=5655&& set N_ENVS=8&& set SYNC_INTERVAL_S=360&& fleet\local\run_distributed_worker.cmd >> data\logs\worker_workhorse1.log 2>&1"
echo Started WH1 worker detached. Tail: type data\logs\worker_workhorse1.log
