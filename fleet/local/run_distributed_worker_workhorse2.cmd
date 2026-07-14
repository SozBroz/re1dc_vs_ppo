@echo off
REM workhorse2 remote worker — 32 envs, learner on WH1 (192.168.0.160)
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE_NAME=workhorse2
set LEARNER_HOST=192.168.0.160
set BASE_PORT=5555
set N_ENVS=32
set SYNC_INTERVAL_S=180

if not exist data\logs mkdir data\logs

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
  --capture-checkpoints ^
  --headless ^
  --screenshot-mmf ^
  --n-steps 768 ^
  --inference-batch-max %N_ENVS% >> data\logs\worker_workhorse2.log 2>&1
