@echo off
REM WH2 learner — planner-loyal, resume latest zip. Local 28-env worker.
setlocal
cd /d C:\Users\sshuser\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE=workhorse2
set LEARNER_PORT=%FLEET_LEARNER_PORT%
set SYNC_INTERVAL_S=360
set BATCH_SIZE=3072
set MAX_PENDING_STEPS=160000
set MIN_HOST_FREE_GB=16

call "%~dp0planner_loyal.env.cmd"
if not exist data\logs mkdir data\logs
if not exist data\checkpoints mkdir data\checkpoints

call "%~dp0flush_log.cmd" data\logs\learner_wh2_planner_loyal.log

echo Starting WH2 local planner-loyal worker...
start "wh2-planner-loyal-worker" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_worker_workhorse2_planner_loyal.cmd"

echo [%DATE% %TIME%] WH2 planner-loyal learner start batch=%BATCH_SIZE% >> data\logs\learner_wh2_planner_loyal.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role learner ^
  --machine-name %MACHINE% ^
  --run-name planner_loyal_shield_key ^
  --curriculum curriculum\planner_loyal_one_leg.json ^
  --learner-port %LEARNER_PORT% ^
  --bind-host 0.0.0.0 ^
  --total-steps 0 ^
  --training-speed 6400 ^
  --skip-chunk 600 ^
  --capture-checkpoints ^
  --sync-interval-s %SYNC_INTERVAL_S% ^
  --max-staleness 1 ^
  --relevance-gate ^
  --batch-size %BATCH_SIZE% ^
  --max-pending-steps %MAX_PENDING_STEPS% ^
  --min-host-free-gb %MIN_HOST_FREE_GB% ^
  --resume auto ^
  --no-local-worker ^
  --headless >> data\logs\learner_wh2_planner_loyal.log 2>&1
