@echo off
REM Keep learner-only alive on WH2 (no local BizHawk).
cd /d C:\Users\sshuser\re1_rl
if not exist data\logs mkdir data\logs
:loop
echo [%DATE% %TIME%] watchdog starting learner >> data\logs\learner_watchdog.log
call fleet\local\run_distributed_learner_workhorse2.cmd >> data\logs\learner_at_logon.log 2>&1
echo [%DATE% %TIME%] learner exited errorlevel=%ERRORLEVEL% — restart in 10s >> data\logs\learner_watchdog.log
timeout /t 10 /nobreak >nul
goto loop
