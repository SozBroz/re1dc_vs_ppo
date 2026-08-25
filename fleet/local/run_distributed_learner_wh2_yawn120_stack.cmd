@echo off
REM RETIRED: fleet learner is WH3. Keep as WH2 fallback (explicit batch 3072).
REM WH2 learner + yawn120 worker only (no legacy local worker).
setlocal
cd /d C:\Users\sshuser\re1_rl
set MACHINE=workhorse2
set LEARNER_PORT=8765
set SYNC_INTERVAL_S=360

call "%~dp0go_explore_phase_c.env.cmd"
if not exist data\go_explore mkdir data\go_explore
if not exist data\logs mkdir data\logs

call "%~dp0flush_log.cmd" data\logs\learner_wh2_yawn120.log

start "wh2-yawn120-worker" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_worker_workhorse2_yawn120.cmd"

venv\Scripts\python.exe scripts\distributed_train_parallel.py ^
  --role learner ^
  --machine-name %MACHINE% ^
  --run-name reward_tune_1040k ^
  --curriculum curriculum/yawn_rails_one_leg.json ^
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
  --headless >> data\logs\learner_wh2_yawn120.log 2>&1
