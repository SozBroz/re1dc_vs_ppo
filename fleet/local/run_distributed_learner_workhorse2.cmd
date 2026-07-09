@echo off
REM workhorse2 learner — repo at C:\Users\sshuser\re1_rl (no D: drive)
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE=workhorse2
set RUN=reward_tune_1040k
set N_ENVS=20
set BASE_PORT=5555
set LEARNER_PORT=8765

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
  --batch-threshold 20480 ^
  --max-staleness 40 ^
  --resume data/checkpoints/reward_tune_1040k/ppo_re1_11160000_steps.zip
