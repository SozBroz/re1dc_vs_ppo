@echo off
REM Detached WH2 planner-loyal learner stack.
setlocal
cd /d C:\Users\sshuser\re1_rl
start "wh2-planner-loyal-stack" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_learner_wh2_planner_loyal_stack.cmd"
echo Started WH2 planner-loyal stack. Tail: type data\logs\learner_wh2_planner_loyal.log
