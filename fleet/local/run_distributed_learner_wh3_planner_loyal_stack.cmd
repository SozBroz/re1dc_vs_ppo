@echo off
REM WH3 learner — planner-loyal; batch 4096 on 5090; resume from copied WH2 ckpt.
setlocal
cd /d C:\Users\sshuser\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE=workhorse3
set LEARNER_PORT=%FLEET_LEARNER_PORT%
set SYNC_INTERVAL_S=360
set BATCH_SIZE=4096
set MAX_PENDING_STEPS=220000
set MIN_HOST_FREE_GB=16

call "%~dp0planner_loyal.env.cmd"
if not exist data\logs mkdir data\logs
if not exist data\checkpoints mkdir data\checkpoints

call "%~dp0flush_log.cmd" data\logs\learner_wh3_planner_loyal.log

echo Starting WH3 local planner-loyal worker via interactive scheduled task...
start "wh3-pl-worker-sched" /MIN powershell -NoProfile -ExecutionPolicy Bypass -File fleet\local\start_wh3_pl_local_worker.ps1 -DelaySec 45

echo [%DATE% %TIME%] WH3 planner-loyal learner start batch=%BATCH_SIZE% >> data\logs\learner_wh3_planner_loyal.log

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
  --sync-interval-s %SYNC_INTERVAL_S% ^
  --max-staleness 1 ^
  --relevance-gate ^
  --batch-size %BATCH_SIZE% ^
  --max-pending-steps %MAX_PENDING_STEPS% ^
  --min-host-free-gb %MIN_HOST_FREE_GB% ^
  --resume auto ^
  --no-local-worker ^
  --headless >> data\logs\learner_wh3_planner_loyal.log 2>&1
