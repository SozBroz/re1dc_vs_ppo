@echo off
REM Detached WH3 learner stack (RDP/console on workhorse3).
setlocal
cd /d C:\Users\sshuser\re1_rl
call "%~dp0start_learner_detached_wh3_planner_loyal.cmd"
