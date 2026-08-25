@echo off
REM WH2 local worker — planner-loyal tip→shield_key, thin cells.
setlocal
cd /d C:\Users\sshuser\re1_rl
call "%~dp0..\fleet_hosts.cmd"
call "%~dp0planner_loyal.env.cmd"
set MACHINE_NAME=workhorse2
set LEARNER_HOST=127.0.0.1
set BASE_PORT=5555
set N_ENVS=16
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360
set RE1_MACHINE_NAME=%MACHINE_NAME%

if not exist data\logs mkdir data\logs
call "%~dp0flush_log.cmd" data\logs\worker_wh2_planner_loyal.log
echo [%DATE% %TIME%] planner-loyal start n_envs=%N_ENVS% LEG_REPLAY=%RE1_YAWN_LEG_REPLAY% >> data\logs\worker_wh2_planner_loyal.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id workhorse2-planner-loyal --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --curriculum curriculum\yawn_rails_one_leg.json --n-envs %N_ENVS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --headless --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_wh2_planner_loyal.log 2>&1
