@echo off
REM Detached WH2 planner-loyal remote worker (28 envs). Learner is WH3.
setlocal
cd /d C:\Users\sshuser\re1_rl
if not exist data\logs mkdir data\logs
call "%~dp0flush_log.cmd" data\logs\worker_wh2_planner_loyal.log
start "wh2-planner-loyal-worker" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_worker_workhorse2_planner_loyal.cmd"
echo Started workhorse2-planner-loyal. Tail: type data\logs\worker_wh2_planner_loyal.log
