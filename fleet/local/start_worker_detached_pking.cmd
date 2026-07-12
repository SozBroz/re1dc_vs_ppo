@echo off
cd /d D:\re1_rl
if not exist data\logs mkdir data\logs
taskkill /F /IM EmuHawk.exe >nul 2>&1
REM Kill only obvious training workers; leave IDE/other python alone when possible
for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe' and CommandLine like '%%distributed_train%%'" get ProcessId /format:csv ^| findstr /r "[0-9]"') do taskkill /F /PID %%P >nul 2>&1
timeout /t 2 /nobreak >nul
set MACHINE_NAME=pking
set LEARNER_HOST=192.168.0.111
set BASE_PORT=5755
set N_ENVS=18
set SYNC_INTERVAL_S=360
start "pking-worker" /MIN cmd /c "cd /d D:\re1_rl && fleet\local\run_distributed_worker_pking.cmd >> data\logs\worker_pking.log 2>&1"
echo Started pking worker detached. Tail: type data\logs\worker_pking.log
