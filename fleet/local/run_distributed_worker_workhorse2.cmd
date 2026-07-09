@echo off
REM workhorse2 local worker (16 envs — 28 threads, ~32GB RAM)
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE_NAME=workhorse2
set LEARNER_HOST=127.0.0.1
set N_ENVS=16
set BASE_PORT=5555

venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role worker ^
  --machine-name %MACHINE_NAME% ^
  --learner-host %LEARNER_HOST% ^
  --learner-port 8765 ^
  --n-envs %N_ENVS% ^
  --base-port %BASE_PORT% ^
  --total-steps 0 ^
  --training-speed 6400 ^
  --skip-chunk 600 ^
  --weight-sync-poll-s 360 ^
  --capture-checkpoints
