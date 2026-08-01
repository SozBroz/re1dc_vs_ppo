@echo off
REM Single pking env on the memlog port (5759). Learner must already be up.
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=pking
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5759
set N_ENVS=1
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360

set RE1_STEP_DIAG_PORT=5759
set RE1_MACHINE_NAME=%MACHINE_NAME%
set RE1_STEP_DIAG_LOG=D:\re1_rl\data\logs\pking_top_right_memlog.jsonl

if not exist data\logs mkdir data\logs
call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_memlog_once.log"

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
  --no-headless ^
  --screenshot-mmf ^
  --inference-batch-max %N_ENVS% ^
  --tile-windows ^
  --grid-cols 1 ^
  --grid-rows 1
