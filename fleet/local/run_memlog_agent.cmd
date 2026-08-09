@echo off
REM Persistent independent diagnostic actor: logical rank 4, TCP port 5759.
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"

set MACHINE_NAME=pking
set LEARNER_HOST=%FLEET_LEARNER_HOST%
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360

set RE1_STEP_DIAG_PORT=5759
set RE1_MACHINE_NAME=%MACHINE_NAME%
set RE1_STEP_DIAG_LOG=D:\re1_rl\data\logs\pking_top_right_memlog.jsonl
REM Same pin as train workers while hunting cp34 (gallery complete + crest).
call "%~dp0go_explore_phase_c.env.cmd"
set RE1_YAWN_RESET_PIN_INDEX=33
if not exist data\go_explore mkdir data\go_explore

if not exist data\logs mkdir data\logs
if not exist data\memlog mkdir data\memlog

venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role worker ^
  --machine-name %MACHINE_NAME% ^
  --worker-id pking-memlog ^
  --learner-host %LEARNER_HOST% ^
  --learner-port %FLEET_LEARNER_PORT% ^
  --curriculum curriculum/yawn_rails_one_leg.json ^
  --n-envs 1 ^
  --actor-ranks 4 ^
  --memlog ^
  --base-port 5755 ^
  --total-steps 0 ^
  --training-speed 6400 ^
  --skip-chunk 600 ^
  --sync-interval-s %SYNC_INTERVAL_S% ^
  --capture-checkpoints ^
  --no-headless ^
  --tile-windows ^
  --grid-cols 5 ^
  --grid-rows 4 ^
  --screenshot-mmf ^
  --inference-batch-max 1
