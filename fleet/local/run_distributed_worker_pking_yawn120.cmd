@echo off
REM pking -> WH2 learner: grind cp120->cp121 with leg-replay capture ON.
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=pking
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5855
set N_ENVS=8
set ACTOR_RANKS=0-7
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360

set RE1_GRID_BOTTOM_INSET=48
set RE1_GRID_CHROMELESS_SHELLS=1
set RE1_PB_CAPTURE=1
set RE1_PB_V1_TYPEWRITER_ONLY=1
set RE1_PB_DANGER_ROOMS=1
set RE1_MACHINE_NAME=%MACHINE_NAME%

call "%~dp0go_explore_phase_c.env.cmd"
call "%~dp0yawn_cp120_grind.env.cmd"
if not exist data\go_explore mkdir data\go_explore

call "%~dp0flush_log.cmd" data\logs\worker_pking_yawn120.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id pking-yawn120 --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --curriculum curriculum/yawn_rails_one_leg.json --n-envs %N_ENVS% --actor-ranks %ACTOR_RANKS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --headless --tile-windows --grid-cols 4 --grid-rows 2 --grid-monitor right --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_pking_yawn120.log 2>&1
