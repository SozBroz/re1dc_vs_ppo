@echo off
REM workhorse2 learner — 28 local envs (Doc04 medium RAM/VRAM budget); sync 360
REM Headroom: @32 envs 10m soak peak ~41GB used / ~24GB free (pages_input~0).
REM Package: sync 360 wall + n_steps=1125 + batch_size=3072 + n_epochs=4.
REM Memory bound: --max-pending-steps 100000 (default) + --worker-buffer-steps 32000
REM (early flush); capacity_full backpressure pauses workers until next cohort/policy.
REM Overlap: learner reopens admission at train start; --min-host-free-gb 12.
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE=workhorse2
set RUN=reward_tune_1040k
set N_ENVS=28
set BASE_PORT=5555
set LEARNER_PORT=8765
set SYNC_INTERVAL_S=360

REM PB champions: typewriter saves + west-wing danger-room first-entry (108/202/204).
REM Reset mix: sample_training_start — RE1_PB_FRESH_WEIGHT ignored (legacy).
set RE1_PB_CAPTURE=1
set RE1_PB_V1_TYPEWRITER_ONLY=1
set RE1_PB_DANGER_ROOMS=1

call "%~dp0go_explore_phase_c.env.cmd"
call "%~dp0workhorse_reset_cp44.env.cmd"
REM Equal mix: dining-room fresh + uniform loadable cp00–cp95 (hot-reload via pin file).
set RE1_YAWN_RESET_PIN_FILE=C:\Users\sshuser\re1_rl\data\workhorse2_reset_pin.env
set RE1_YAWN_RESET_PIN_SET=
set RE1_YAWN_RESET_PIN_SET_WEIGHT=
set RE1_YAWN_RESET_PIN_RANGE=
set RE1_YAWN_RESET_PIN_INCLUDE_FRESH=1
if not exist data\go_explore mkdir data\go_explore

if not exist data\logs mkdir data\logs
REM Drop wedged PB sync locks before learner/workers come up.
powershell -NoProfile -Command ^
  "$root='C:\Users\sshuser\re1_rl\states\pb\champions'; if (Test-Path $root) { Get-ChildItem $root -Directory -EA SilentlyContinue | ForEach-Object { Remove-Item -Force (Join-Path $_.FullName 'champion.sync.lock') -EA SilentlyContinue; Remove-Item -Recurse -Force (Join-Path $_.FullName '.incoming') -EA SilentlyContinue } }"
REM Fresh heuristics log for this batch (truncate; do not delete).
call "%~dp0flush_log.cmd" "C:\Users\sshuser\re1_rl\data\logs\learner_wh2_25.log"
call "%~dp0flush_log.cmd" "C:\Users\sshuser\re1_rl\data\logs\worker_wh2_local.log"
echo [%DATE% %TIME%] run_distributed_learner_wh2_25.cmd launching learner>> data\logs\learner_wh2_25.log

REM Keep BizHawk startup out of the CUDA learner process. The worker waits for
REM learner weights, supervises/restarts its own actors, and uses the same host.
REM WH2's GDI/chromeless initialization can connect TCP yet stall before Lua.
start "wh2-local-worker" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE% --worker-id workhorse2 --learner-host 127.0.0.1 --learner-port %LEARNER_PORT% --curriculum curriculum/yawn_rails_one_leg.json --n-envs %N_ENVS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --capture-checkpoints --sync-interval-s %SYNC_INTERVAL_S% --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_wh2_local.log 2>&1"

venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role learner ^
  --machine-name %MACHINE% ^
  --run-name %RUN% ^
  --curriculum curriculum/yawn_rails_one_leg.json ^
  --n-envs %N_ENVS% ^
  --base-port %BASE_PORT% ^
  --learner-port %LEARNER_PORT% ^
  --bind-host 0.0.0.0 ^
  --total-steps 0 ^
  --training-speed 6400 ^
  --skip-chunk 600 ^
  --capture-checkpoints ^
  --sync-interval-s %SYNC_INTERVAL_S% ^
  --max-staleness 1 ^
  --relevance-gate ^
  --batch-size 3072 ^
  --min-host-free-gb 12 ^
  --resume auto ^
  --no-local-worker ^
  --headless ^
  --screenshot-mmf ^
  --inference-batch-max %N_ENVS% >> data\logs\learner_wh2_25.log 2>&1
