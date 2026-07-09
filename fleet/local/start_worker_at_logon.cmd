@echo off
REM Runs at interactive logon (Scheduled Task). Starts WH1/pking-style remote worker.
setlocal
cd /d D:\re1_rl 2>nul
if errorlevel 1 cd /d C:\Users\sshuser\re1_rl

if not exist data\logs mkdir data\logs
echo [%DATE% %TIME%] at_logon worker start >> data\logs\at_logon.log

REM Give explorer/desktop a moment
timeout /t 20 /nobreak >nul

if /I "%COMPUTERNAME%"=="AI_MACHINE" goto wh1
if /I "%COMPUTERNAME%"=="WORKHORSE1" goto wh1
if /I "%COMPUTERNAME%"=="WORKHORSE2" goto wh2
goto pking

:wh1
set MACHINE_NAME=workhorse1
set LEARNER_HOST=192.168.0.111
set BASE_PORT=5655
set N_ENVS=8
goto run

:wh2
set MACHINE_NAME=workhorse2
set LEARNER_HOST=127.0.0.1
set BASE_PORT=5555
set N_ENVS=8
goto run

:pking
set MACHINE_NAME=pking
set LEARNER_HOST=192.168.0.111
set BASE_PORT=5755
set N_ENVS=12

:run
set SYNC_INTERVAL_S=360

taskkill /F /IM EmuHawk.exe >nul 2>&1
timeout /t 2 /nobreak >nul

call fleet\local\run_distributed_worker.cmd >> data\logs\worker_at_logon.log 2>&1
