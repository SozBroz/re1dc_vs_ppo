@echo off
REM workhorse2 learner — 32 local envs (MMF screenshots); sync 180
REM Headroom: soak @27 peaked ~32GB used / 33GB free. Live @32: ~45GB used / ~19GB free,
REM pages_input=0, pagefile~100MB. Train spikes historically +3-6GB python — still >12GB free.
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE=workhorse2
set RUN=reward_tune_1040k
set N_ENVS=32
set BASE_PORT=5555
set LEARNER_PORT=8765
set SYNC_INTERVAL_S=180

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
  --n-steps 768 ^
  --inference-batch-max %N_ENVS% >> data\logs\learner_wh2_25.log 2>&1
