@echo off
REM Optional separate WH2 remote-style worker (loopback). Prefer learner --role learner
REM with co-located local worker (no HTTP). Kept for debugging.
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE_NAME=workhorse2
set LEARNER_HOST=127.0.0.1
set N_ENVS=8
set BASE_PORT=5555
set SYNC_INTERVAL_S=360

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
  --sync-interval-s %SYNC_INTERVAL_S% ^
  --capture-checkpoints
