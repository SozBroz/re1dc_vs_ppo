@echo off
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
if "%RE1_MEMLOG_DASHBOARD_PORT%"=="" set RE1_MEMLOG_DASHBOARD_PORT=8787
venv\Scripts\python.exe scripts\memlog_dashboard.py --bind 127.0.0.1 --port %RE1_MEMLOG_DASHBOARD_PORT% --learner-url http://%FLEET_LEARNER_HOST%:%FLEET_LEARNER_PORT%
