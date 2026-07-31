@echo off
REM pking → WH2 learner — headless chromeless EmuHawk + monitor grid (throughput path).
REM Memlog + forced visible: run_distributed_worker_pking_visible.cmd
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=pking
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5755
set N_ENVS=20
if "%SYNC_INTERVAL_S%"=="" set SYNC_INTERVAL_S=360

REM PB champions: typewriter saves + west-wing danger-room first-entry (108/202/204).
set RE1_PB_CAPTURE=1
set RE1_PB_V1_TYPEWRITER_ONLY=1
set RE1_PB_DANGER_ROOMS=1

call "%~dp0go_explore_phase_c.env.cmd"
if not exist data\go_explore mkdir data\go_explore

venv\Scripts\python.exe scripts\distributed_train_parallel.py --role worker --machine-name %MACHINE_NAME% --learner-host %LEARNER_HOST% --learner-port %FLEET_LEARNER_PORT% --n-envs %N_ENVS% --base-port %BASE_PORT% --total-steps 0 --training-speed 6400 --skip-chunk 600 --sync-interval-s %SYNC_INTERVAL_S% --capture-checkpoints --headless --tile-windows --grid-cols 5 --grid-rows 4 --screenshot-mmf --n-steps 1536 --inference-batch-max %N_ENVS%
