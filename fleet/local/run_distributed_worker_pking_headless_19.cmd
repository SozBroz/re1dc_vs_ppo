@echo off
REM pking headless 19-env alt — ranks 0-3,5-19; rank 4 / port 5759 reserved for memlog.
REM Default throughput path is run_distributed_worker_pking.cmd (20 headless).
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=pking
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5755
set N_ENVS=19
set ACTOR_RANKS=0-3,5-19
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360

set RE1_GRID_BOTTOM_INSET=48
set RE1_GRID_CHROMELESS_SHELLS=1
set RE1_PB_CAPTURE=1
set RE1_PB_V1_TYPEWRITER_ONLY=1
set RE1_PB_DANGER_ROOMS=1
set RE1_STEP_DIAG_PORT=5759
set RE1_MACHINE_NAME=%MACHINE_NAME%

call "%~dp0go_explore_phase_c.env.cmd"
set RE1_YAWN_RESET_FRONTIER_FIGHT_ONLY=
set RE1_YAWN_RESET_PIN_INDEX=
set RE1_YAWN_RESET_PIN_SET=
set RE1_YAWN_RESET_PIN_SET_WEIGHT=
set RE1_YAWN_RESET_PIN_RANGE=0-55
if not exist data\go_explore mkdir data\go_explore

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id pking --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --curriculum curriculum/yawn_rails_one_leg.json --n-envs %N_ENVS% --actor-ranks %ACTOR_RANKS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --headless --tile-windows --grid-cols 5 --grid-rows 4 --grid-monitor right --screenshot-mmf --inference-batch-max %N_ENVS%
