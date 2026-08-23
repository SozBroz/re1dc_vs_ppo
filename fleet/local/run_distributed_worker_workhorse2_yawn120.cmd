@echo off
REM WH2 local worker only: grind cp120->cp121 (learner stays up).
setlocal
cd /d C:\Users\sshuser\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=workhorse2
set LEARNER_HOST=127.0.0.1
set BASE_PORT=5555
set N_ENVS=28
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360

set RE1_ACTOR_STARTUP_BATCH_SIZE=1
set RE1_ACTOR_STARTUP_STAGGER_S_PER_RANK=0
set RE1_EMUHAWK_DETACH_CONSOLE=1
set RE1_EMUHAWK_START_PROCESS=1
set RE1_PB_CAPTURE=1
set RE1_PB_V1_TYPEWRITER_ONLY=1
set RE1_PB_DANGER_ROOMS=1
set RE1_MACHINE_NAME=%MACHINE_NAME%

call "%~dp0go_explore_phase_c.env.cmd"
call "%~dp0yawn_cp120_grind.env.cmd"
set RE1_YAWN_RESET_PIN_FILE=C:\Users\sshuser\re1_rl\data\workhorse2_reset_pin.env
if not exist data\go_explore mkdir data\go_explore
if not exist data\logs mkdir data\logs

call "%~dp0flush_log.cmd" data\logs\worker_wh2_yawn120.log

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --worker-id workhorse2-yawn120 --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --curriculum curriculum/yawn_rails_one_leg.json --n-envs %N_ENVS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --headless --screenshot-mmf --inference-batch-max %N_ENVS% >> data\logs\worker_wh2_yawn120.log 2>&1
