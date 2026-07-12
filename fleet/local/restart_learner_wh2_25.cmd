@echo off
REM Kill stale learner/local worker, restart WH2 distributed learner (28 local envs).
setlocal
cd /d C:\Users\sshuser\re1_rl
taskkill /F /IM EmuHawk.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'distributed_train' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -Command "Start-Sleep -Seconds 3"
start "WH2-learner" /MIN cmd /c "cd /d C:\Users\sshuser\re1_rl && fleet\local\run_distributed_learner_wh2_25.cmd"
echo WH2 learner restarted. Tail: type data\logs\learner_wh2_25.log
