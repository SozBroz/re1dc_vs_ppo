@echo off
REM Start pking headless training worker (19 envs) and independent memlog (rank 4).
setlocal
cd /d D:\re1_rl

call "%~dp0start_worker_detached_pking.cmd"
call "%~dp0start_worker_detached_pking_memlog.cmd"

echo Started pking headless worker + memlog agent.
