@echo off

REM pking → WH2 learner — visible tiled regular actors; rank 4 is independent memlog.

REM Headless production: run_distributed_worker_pking.cmd (no RE1_STEP_DIAG_*).

setlocal

cd /d D:\re1_rl

call "%~dp0..\fleet_hosts.cmd"

set MACHINE_NAME=pking

set LEARNER_HOST=%FLEET_LEARNER_HOST%

set BASE_PORT=5755

set N_ENVS=19

set ACTOR_RANKS=0-3,5-19

if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360



set RE1_PB_CAPTURE=1

set RE1_PB_V1_TYPEWRITER_ONLY=1

set RE1_PB_DANGER_ROOMS=1



REM The regular worker does not own 5759; this only lets its shared tiler label
REM the independently managed window in the reserved top-right seat.
set RE1_STEP_DIAG_PORT=5759
set RE1_MACHINE_NAME=%MACHINE_NAME%



call "%~dp0flush_log.cmd" data\logs\worker_pking.log



venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id pking --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --n-envs %N_ENVS% --actor-ranks %ACTOR_RANKS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --no-headless --tile-windows --grid-cols 5 --grid-rows 4 --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_pking.log 2>&1

