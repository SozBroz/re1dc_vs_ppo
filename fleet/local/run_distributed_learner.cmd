@echo off
REM Learner + local worker on workhorse2 (192.168.0.116)
setlocal
cd /d D:\re1_rl
set MACHINE=workhorse2
set RUN=reward_tune_1040k
set N_ENVS=8
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
REM  Omit --resume to auto-pick latest.json via resolve_resume_path
