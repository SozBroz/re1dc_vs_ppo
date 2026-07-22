@echo off
REM workhorse2 as remote-only worker (legacy; learner normally runs on WH2).
setlocal
cd /d C:\Users\sshuser\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=workhorse2
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5555
set N_ENVS=24
set SYNC_INTERVAL_S=360

if not exist data\logs mkdir data\logs

venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role worker ^
  --machine-name %MACHINE_NAME% ^
  --learner-host %LEARNER_HOST% ^
  --learner-port %FLEET_LEARNER_PORT% ^
  --n-envs %N_ENVS% ^
  --base-port %BASE_PORT% ^
  --total-steps 0 ^
  --training-speed 6400 ^
  --skip-chunk 600 ^
  --sync-interval-s %SYNC_INTERVAL_S% ^
  --capture-checkpoints ^
  --headless ^
  --screenshot-mmf ^
  --n-steps 1536 ^
  --inference-batch-max %N_ENVS% >> data\logs\worker_workhorse2.log 2>&1
