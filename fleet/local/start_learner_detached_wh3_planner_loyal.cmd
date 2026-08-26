@echo off
REM Detached WH3 planner-loyal learner stack.
setlocal
cd /d C:\Users\sshuser\re1_rl
start "wh3-planner-loyal-stack" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_learner_wh3_planner_loyal_stack.cmd"
echo Started WH3 planner-loyal stack. Tail: type data\logs\learner_wh3_planner_loyal.log
