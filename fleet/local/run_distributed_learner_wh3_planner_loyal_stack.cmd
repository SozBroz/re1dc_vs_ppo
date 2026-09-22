@echo off
REM WH3 learner — planner-loyal; batch 4096 on 5090; resume from copied WH2 ckpt.
setlocal
cd /d C:\Users\sshuser\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE=workhorse3
set LEARNER_PORT=%FLEET_LEARNER_PORT%
REM Soft-grind (interact drought): 180s cadence via --grind (buffer 16000,
REM grace 60s implied); batch 4096 x 3 epochs; adaptive cap uses the full wall
REM until this run mints, then 3x the best measured emulated-frame duration.
REM Revert: drop --grind --per-tip-cap --adaptive-cap, restore SYNC 360 / BATCH 8192.
set SYNC_INTERVAL_S=180
set BATCH_SIZE=4096
set MAX_PENDING_STEPS=220000
set MIN_HOST_FREE_GB=16

call "%~dp0planner_loyal.env.cmd"
REM Human BC for pl83->pl84 armor_vent_far: only open-loop-verified demos
REM under data\demos\planner_loyal (4x Sep-6 tapes that pay far on live pl83).
set RE1_BC_DEMO_DIR=data\demos\planner_loyal
set RE1_BC_COEF=0.5
set RE1_BC_RELOAD_EVERY=20
echo [%DATE% %TIME%] BC ON dir=%RE1_BC_DEMO_DIR% coef=%RE1_BC_COEF% >> data\logs\learner_wh3_planner_loyal.log
if not exist data\logs mkdir data\logs
if not exist data\checkpoints mkdir data\checkpoints

call "%~dp0flush_log.cmd" data\logs\learner_wh3_planner_loyal.log

REM C-RE1 only: do not start the BizHawk local worker.
REM echo Starting WH3 local planner-loyal worker via interactive scheduled task...
REM start "wh3-pl-worker-sched" /MIN powershell -NoProfile -ExecutionPolicy Bypass -File fleet\local\start_wh3_pl_local_worker.ps1 -DelaySec 45

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
  --grind ^
  --per-tip-cap ^
  --adaptive-cap ^
  --headless >> data\logs\learner_wh3_planner_loyal.log 2>&1
