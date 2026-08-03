@echo off
REM pking capture canary — visible tiled BizHawk + Go-Explore capture ON (WH1/WH2 stay off).
REM Restart only pking after flip: fleet\local\start_worker_detached_pking_capture_canary.cmd
REM 24h soak: cells/ on pking should stay ~0 (ephemeral); WH2 learner cells/ grows <= budget.
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

call "%~dp0go_explore_phase_c.env.cmd"
call "%~dp0go_explore_capture_on.env.cmd"
if not exist data\go_explore mkdir data\go_explore

set RE1_STEP_DIAG_PORT=5759
set RE1_MACHINE_NAME=%MACHINE_NAME%
set RE1_STEP_DIAG_LOG=D:\re1_rl\data\logs\pking_top_right_memlog.jsonl

call "%~dp0flush_log.cmd" data\logs\worker_pking.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id pking --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --curriculum curriculum/yawn_rails_one_leg.json --n-envs %N_ENVS% --actor-ranks %ACTOR_RANKS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --no-headless --tile-windows --grid-cols 5 --grid-rows 4 --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_pking.log 2>&1
