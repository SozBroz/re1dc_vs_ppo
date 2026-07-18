@echo off
cd /d C:\Users\sshuser\re1_rl
echo START %DATE% %TIME%>> data\logs\learner_startup_probe.log
venv\Scripts\python.exe scripts\distributed_train_parallel.py --role learner --machine-name workhorse2 --run-name reward_tune_1040k --n-envs 28 --base-port 5555 --learner-port 8765 --bind-host 0.0.0.0 --total-steps 0 --training-speed 6400 --skip-chunk 600 --capture-checkpoints --sync-interval-s 360 --max-staleness 2 --resume auto --headless --screenshot-mmf --inference-batch-max 28 >> data\logs\learner_startup_probe.log 2>&1
echo END %DATE% %TIME% exit=%ERRORLEVEL%>> data\logs\learner_startup_probe.log
