@echo off
cd /d C:\Users\sshuser\re1_rl
if not exist data\logs mkdir data\logs
venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role learner ^
  --machine-name workhorse2 ^
  --run-name reward_tune_1040k ^
  --resume data/checkpoints/reward_tune_1040k/ppo_re1_11160000_steps.zip ^
  --n-envs 20 ^
  --base-port 5555 ^
  --learner-port 8765 ^
  --bind-host 0.0.0.0 ^
  --total-steps 0 ^
  --training-speed 6400 ^
  --skip-chunk 600 ^
  --capture-checkpoints ^
  --sync-interval-s 360 ^
  --max-staleness 2 >> data\logs\learner_wh2.log 2>&1
