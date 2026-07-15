@echo off
REM workhorse1 remote worker — MUST run from interactive RDP/console, not bare SSH.
REM Yesterday: registered over HTTP but EmuHawk/Lua never connected from SSH session.
setlocal
cd /d D:\re1_rl
set MACHINE_NAME=workhorse1
set LEARNER_HOST=192.168.0.116
set BASE_PORT=5655
set N_ENVS=8
set SYNC_INTERVAL_S=180

if not exist data\logs mkdir data\logs

echo Killing leftover EmuHawk/python on WH1...
taskkill /F /IM EmuHawk.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Starting WH1 worker: %N_ENVS% envs ports %BASE_PORT%+ -> learner %LEARNER_HOST%:8765
echo Log: data\logs\worker_workhorse1.log
call fleet\local\run_distributed_worker.cmd >> data\logs\worker_workhorse1.log 2>&1
