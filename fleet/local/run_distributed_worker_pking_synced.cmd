@echo off
REM pking → WH2 learner — SYNCED SubprocVecEnv lockstep (experiment A/B vs desync)
REM Same knobs as run_distributed_worker_pking.cmd except --synced-envs.
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=pking
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5755
set N_ENVS=20
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=180

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
  --n-steps 768 ^
  --inference-batch-max %N_ENVS% ^
  --synced-envs ^
  --tile-windows ^
  --grid-cols 5 ^
  --grid-rows 4
