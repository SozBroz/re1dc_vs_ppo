@echo off
REM Runs at interactive logon on WH2 — learner + local 8-env fleet.
REM Detached start so the scheduled task does not leave a stuck second instance.
setlocal
cd /d C:\Users\sshuser\re1_rl 2>nul
if errorlevel 1 cd /d D:\re1_rl

if not exist data\logs mkdir data\logs
echo [%DATE% %TIME%] at_logon learner start >> data\logs\at_logon.log

REM Give explorer/desktop a moment
timeout /t 20 /nobreak >nul

taskkill /F /IM EmuHawk.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 3 /nobreak >nul

start "WH2-learner" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_learner_workhorse2.cmd >> data\logs\learner_at_logon.log 2>&1"
echo [%DATE% %TIME%] at_logon learner detached >> data\logs\at_logon.log
