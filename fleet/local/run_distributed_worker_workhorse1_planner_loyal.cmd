@echo off
REM WH1 remote worker — planner-loyal.
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
call "%~dp0planner_loyal.env.cmd"
set MACHINE_NAME=workhorse1
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5655
set N_ENVS=8
set ACTOR_RANKS=0-7
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360
set RE1_MACHINE_NAME=%MACHINE_NAME%

if not exist data\logs mkdir data\logs
call "%~dp0flush_log.cmd" data\logs\worker_workhorse1_planner_loyal.log
echo [%DATE% %TIME%] planner-loyal start n_envs=%N_ENVS% learner=%LEARNER_HOST% >> data\logs\worker_workhorse1_planner_loyal.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id workhorse1-planner-loyal --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --curriculum curriculum\planner_loyal_one_leg.json --n-envs %N_ENVS% --actor-ranks %ACTOR_RANKS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --headless --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_workhorse1_planner_loyal.log 2>&1
