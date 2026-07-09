@echo off
REM Runs at interactive logon on WH2 — learner + local 8-env fleet.
setlocal
cd /d C:\Users\sshuser\re1_rl 2>nul
if errorlevel 1 cd /d D:\re1_rl

if not exist data\logs mkdir data\logs
echo [%DATE% %TIME%] at_logon learner start >> data\logs\at_logon.log
timeout /t 30 /nobreak >nul

taskkill /F /IM EmuHawk.exe >nul 2>&1
timeout /t 2 /nobreak >nul

call fleet\local\run_distributed_learner_workhorse2.cmd >> data\logs\learner_at_logon.log 2>&1
