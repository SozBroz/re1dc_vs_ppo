@echo off
REM workhorse2 learner — 24 local envs (Doc04 medium RAM/VRAM budget); sync 360
REM Headroom: @32 envs 10m soak peak ~41GB used / ~24GB free (pages_input~0).
REM Package: sync 360 wall + n_steps=1536 (~205s emu / ~4.5 γ HL) + batch_size=4096 + n_epochs=4.
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE=workhorse2
set RUN=reward_tune_1040k
set N_ENVS=24
set BASE_PORT=5555
set LEARNER_PORT=8765
set SYNC_INTERVAL_S=360

REM Typewriter PB champion (single shared slot; capture on legal Main Hall save).
set RE1_PB_CAPTURE=1
set RE1_PB_V1_TYPEWRITER_ONLY=1
set RE1_PB_FRESH_WEIGHT=0.5
set RE1_PB_SHARED_ROOT=C:\Users\sshuser\re1_rl\states\pb

if not exist data\logs mkdir data\logs
REM Drop wedged PB sync locks before learner/workers come up.
powershell -NoProfile -Command ^
  "$root='C:\Users\sshuser\re1_rl\states\pb\champions'; if (Test-Path $root) { Get-ChildItem $root -Directory -EA SilentlyContinue | ForEach-Object { Remove-Item -Force (Join-Path $_.FullName 'champion.sync.lock') -EA SilentlyContinue; Remove-Item -Recurse -Force (Join-Path $_.FullName '.incoming') -EA SilentlyContinue } }"
REM Fresh heuristics log for this batch (truncate; do not delete).
call "%~dp0flush_log.cmd" "C:\Users\sshuser\re1_rl\data\logs\learner_wh2_25.log"
echo [%DATE% %TIME%] run_distributed_learner_wh2_25.cmd launching learner>> data\logs\learner_wh2_25.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role learner ^
  --machine-name %MACHINE% ^
  --run-name %RUN% ^
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
  --resume auto ^
  --headless ^
  --screenshot-mmf ^
  --n-steps 1536 ^
  --inference-batch-max %N_ENVS% >> data\logs\learner_wh2_25.log 2>&1
