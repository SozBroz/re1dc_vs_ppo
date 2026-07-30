@echo off

REM pking → WH2 learner — visible tiled BizHawk + top-right memlog (debug only).

REM Headless production: run_distributed_worker_pking.cmd (no RE1_STEP_DIAG_*).

setlocal

cd /d D:\re1_rl

call "%~dp0..\fleet_hosts.cmd"

set MACHINE_NAME=pking

set LEARNER_HOST=%FLEET_LEARNER_HOST%

set BASE_PORT=5755

set N_ENVS=20

if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360



set RE1_PB_CAPTURE=1

set RE1_PB_V1_TYPEWRITER_ONLY=1

set RE1_PB_DANGER_ROOMS=1



REM Top-right grid seat (5 cols x 4 rows): rank 4 → port 5759 → slot (col=4,row=0).

set RE1_STEP_DIAG_PORT=5759

set RE1_MACHINE_NAME=%MACHINE_NAME%

set RE1_STEP_DIAG_LOG=D:\re1_rl\data\logs\pking_top_right_memlog.jsonl



call "%~dp0flush_log.cmd" data\logs\worker_pking.log



venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --n-envs %N_ENVS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --no-headless --tile-windows --grid-cols 5 --grid-rows 4 --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_pking.log 2>&1

