@echo off
REM Detached WH3 planner-loyal local worker (24 envs). Muse must already be stopped.
setlocal
cd /d C:\Users\sshuser\re1_rl
if not exist data\logs mkdir data\logs
call "%~dp0flush_log.cmd" data\logs\worker_workhorse3_planner_loyal.log
start "wh3-planner-loyal" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_worker_workhorse3_planner_loyal.cmd"
echo Started workhorse3-planner-loyal. Tail: type data\logs\worker_workhorse3_planner_loyal.log
