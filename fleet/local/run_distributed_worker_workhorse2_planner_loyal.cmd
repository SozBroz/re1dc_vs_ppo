@echo off
REM WH2 remote worker — planner-loyal, 24 envs (learner is WH3).
setlocal
cd /d C:\Users\sshuser\re1_rl
call "%~dp0..\fleet_hosts.cmd"
call "%~dp0planner_loyal.env.cmd"
set MACHINE_NAME=workhorse2
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5555
set N_ENVS=24
set ACTOR_RANKS=0-23
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360
set RE1_MACHINE_NAME=%MACHINE_NAME%
REM Worker-only on 64 GB: 24 is safe; 28 wedged 0 GB free when this box also ran the learner.
set RE1_ACTOR_STARTUP_BATCH_SIZE=2
set RE1_ACTOR_STARTUP_STAGGER_S_PER_RANK=1
set RE1_EMUHAWK_DETACH_CONSOLE=1
set RE1_EMUHAWK_START_PROCESS=1

if not exist data\logs mkdir data\logs
call "%~dp0flush_log.cmd" data\logs\worker_wh2_planner_loyal.log
echo [%DATE% %TIME%] planner-loyal start n_envs=%N_ENVS% LEG_REPLAY=%RE1_YAWN_LEG_REPLAY% >> data\logs\worker_wh2_planner_loyal.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id workhorse2-planner-loyal --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --curriculum curriculum\planner_loyal_one_leg.json --n-envs %N_ENVS% --actor-ranks %ACTOR_RANKS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --headless --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_wh2_planner_loyal.log 2>&1
