@echo off
REM Start pking headless training worker (19 envs) and independent memlog (rank 4).
setlocal
cd /d D:\re1_rl

call "%~dp0start_worker_detached_pking_headless_19.cmd"
call "%~dp0start_worker_detached_pking_memlog.cmd"

echo Started pking headless 19-env worker + memlog agent (rank 4 / port 5759).
