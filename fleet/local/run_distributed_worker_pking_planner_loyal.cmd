@echo off
REM pking remote worker — planner-loyal (20 envs, headless).
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
call "%~dp0planner_loyal.env.cmd"
set MACHINE_NAME=pking
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5755
set N_ENVS=20
set ACTOR_RANKS=0-19
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360
set RE1_MACHINE_NAME=%MACHINE_NAME%
set RE1_GRID_BOTTOM_INSET=48
set RE1_ACTOR_STARTUP_BATCH_SIZE=2
set RE1_ACTOR_STARTUP_STAGGER_S_PER_RANK=1
REM Rank 4 / TCP 5759: live memlog (latest.json + events.jsonl + step diag jsonl).
set RE1_STEP_DIAG_PORT=5759
set RE1_STEP_DIAG_LOG=D:\re1_rl\data\logs\pking_top_right_memlog.jsonl
set RE1_MEMLOG_DIRECTORY=memlog
set RE1_MEMLOG_EXPERIMENT=planner_loyal

if not exist data\logs mkdir data\logs
if not exist data\memlog mkdir data\memlog
call "%~dp0flush_log.cmd" data\logs\worker_pking_planner_loyal.log
echo [%DATE% %TIME%] planner-loyal start n_envs=%N_ENVS% learner=%LEARNER_HOST% memlog=rank4 >> data\logs\worker_pking_planner_loyal.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id pking-planner-loyal --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --curriculum curriculum\planner_loyal_one_leg.json --n-envs %N_ENVS% --actor-ranks %ACTOR_RANKS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --headless --tile-windows --grid-cols 5 --grid-rows 4 --grid-monitor right --screenshot-mmf --inference-batch-max %N_ENVS% --memlog >> data\logs\worker_pking_planner_loyal.log 2>&1
