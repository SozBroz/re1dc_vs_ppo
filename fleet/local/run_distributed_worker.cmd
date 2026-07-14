@echo off
REM Remote worker — set MACHINE and LEARNER_HOST per box
setlocal
cd /d D:\re1_rl
if "%MACHINE_NAME%"=="" set MACHINE_NAME=workhorse1
if "%LEARNER_HOST%"=="" set LEARNER_HOST=192.168.0.111
if "%N_ENVS%"=="" set N_ENVS=12
if "%BASE_PORT%"=="" set BASE_PORT=5655
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=180

venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role worker ^
  --machine-name %MACHINE_NAME% ^
  --learner-host %LEARNER_HOST% ^
  --learner-port 8765 ^
  --n-envs %N_ENVS% ^
  --base-port %BASE_PORT% ^
  --total-steps 0 ^
  --training-speed 6400 ^
  --skip-chunk 600 ^
  --sync-interval-s %SYNC_INTERVAL_S% ^
  --capture-checkpoints ^
  --n-steps 768 ^
  --headless
