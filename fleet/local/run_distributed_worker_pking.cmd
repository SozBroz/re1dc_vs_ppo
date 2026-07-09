@echo off
REM pking dev box worker (ports 5755+)
setlocal
cd /d D:\re1_rl
set MACHINE_NAME=pking
set LEARNER_HOST=192.168.0.111
set BASE_PORT=5755
call fleet\local\run_distributed_worker.cmd
