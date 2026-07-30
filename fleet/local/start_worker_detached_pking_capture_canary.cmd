@echo off
REM Restart pking worker with Go-Explore capture ON (canary). WH1/WH2 unchanged.
setlocal
cd /d D:\re1_rl
if not exist data\logs mkdir data\logs

for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe' and CommandLine like '%%distributed_train%%' and (CommandLine like '%%machine-name pking%%' or CommandLine like '%%base-port 5755%%')" get ProcessId /format:csv 2^>nul ^| findstr /r "[0-9]"') do taskkill /F /PID %%P >nul 2>&1

powershell -NoProfile -Command "$ports = 5755..5774; Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul

powershell -NoProfile -Command "$root='D:\re1_rl\states\pb\champions'; if (Test-Path $root) { Get-ChildItem $root -Directory -EA SilentlyContinue | ForEach-Object { Remove-Item -Force (Join-Path $_.FullName 'champion.sync.lock') -EA SilentlyContinue; Remove-Item -Recurse -Force (Join-Path $_.FullName '.incoming') -EA SilentlyContinue } }"

call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_pking.log"

start "pking-worker-capture-canary" cmd /c "cd /d D:\re1_rl && fleet\local\run_distributed_worker_pking_capture_canary.cmd >> data\logs\worker_pking.log 2>&1"

echo Started pking capture canary (visible). Tail: type data\logs\worker_pking.log
