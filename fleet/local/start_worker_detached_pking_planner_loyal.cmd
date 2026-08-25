@echo off
setlocal
cd /d D:\re1_rl
if not exist data\logs mkdir data\logs
call "%~dp0flush_log.cmd" data\logs\worker_pking_planner_loyal.log
start "pking-planner-loyal" /MIN cmd /c "cd /d D:\re1_rl && fleet\local\run_distributed_worker_pking_planner_loyal.cmd"
echo Started pking-planner-loyal
