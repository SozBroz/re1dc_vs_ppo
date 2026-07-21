@echo off
REM pking → WH2 learner — visible grid for savestate/screenshot/debug
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=pking
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5755
set N_ENVS=20
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360

REM Top-right grid seat (5 cols x 4 rows, row-major, spawn/HWND order ≈ rank):
REM   rank 4 → port 5759 → slot (col=4,row=0). Only that env writes memlog.
REM Disable: unset RE1_STEP_DIAG_PORT (or set empty) before launch.
set RE1_STEP_DIAG_PORT=5759
set RE1_MACHINE_NAME=%MACHINE_NAME%
set RE1_STEP_DIAG_LOG=D:\re1_rl\data\logs\pking_top_right_memlog.jsonl

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
  --n-steps 1536 ^
  --inference-batch-max %N_ENVS% ^
  --tile-windows ^
  --grid-cols 5 ^
  --grid-rows 4