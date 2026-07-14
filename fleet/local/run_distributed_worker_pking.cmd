@echo off
REM pking → WH2 learner — visible grid for savestate/screenshot/debug
setlocal
cd /d D:\re1_rl
set MACHINE_NAME=pking
set LEARNER_HOST=192.168.0.111
set BASE_PORT=5755
set N_ENVS=20
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
  --no-headless ^
  --screenshot-mmf ^
  --n-steps 768 ^
  --inference-batch-max %N_ENVS% ^
  --tile-windows ^
  --grid-cols 5 ^
  --grid-rows 4
