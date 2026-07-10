@echo off
REM pking dev box — visible grid for savestate/screenshot/debug (only non-headless fleet box)
setlocal
cd /d D:\re1_rl
set MACHINE_NAME=pking
set LEARNER_HOST=192.168.0.111
set BASE_PORT=5755
REM Cap at 12 until RAM headroom proven (fleet_setup.md)
set N_ENVS=12
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360

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
  --tile-windows ^
  --grid-cols 4 ^
  --grid-rows 3
