@echo off
REM workhorse2 learner — 28 local envs (MMF screenshots)
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE=workhorse2
set RUN=reward_tune_1040k
set N_ENVS=28
set BASE_PORT=5555
set LEARNER_PORT=8765
set SYNC_INTERVAL_S=360

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
  --inference-batch-max %N_ENVS% >> data\logs\learner_wh2_25.log 2>&1
