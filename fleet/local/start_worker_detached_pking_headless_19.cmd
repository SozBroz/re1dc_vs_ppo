@echo off
REM Restart pking headless worker (19 envs). Preserves pking-memlog on port 5759.
setlocal
cd /d D:\re1_rl
if not exist data\logs mkdir data\logs

for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe' and CommandLine like '%%distributed_train%%' and CommandLine not like '%%worker-id pking-memlog%%' and (CommandLine like '%%machine-name pking%%' or CommandLine like '%%worker-id pking%%' or CommandLine like '%%base-port 5755%%')" get ProcessId /format:csv 2^>nul ^| findstr /r "[0-9]"') do taskkill /F /PID %%P >nul 2>&1

powershell -NoProfile -Command "$ports = 5755..5774 | Where-Object { $_ -ne 5759 }; Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

REM Kill headless train EmuHawks only (not memlog window on 5759).
for /f "tokens=2 delims=," %%P in ('wmic process where "name='EmuHawk.exe'" get ProcessId /format:csv 2^>nul ^| findstr /r "[0-9]"') do (
  powershell -NoProfile -Command "$c=Get-CimInstance Win32_Process -Filter 'ProcessId=%%P' -EA SilentlyContinue; if ($c -and $c.CommandLine -notmatch 'socket_port=5759') { Stop-Process -Id %%P -Force -EA SilentlyContinue }" >nul 2>&1
)

timeout /t 2 /nobreak >nul

powershell -NoProfile -Command "$root='D:\re1_rl\states\pb\champions'; if (Test-Path $root) { Get-ChildItem $root -Directory -EA SilentlyContinue | ForEach-Object { Remove-Item -Force (Join-Path $_.FullName 'champion.sync.lock') -EA SilentlyContinue; Remove-Item -Recurse -Force (Join-Path $_.FullName '.incoming') -EA SilentlyContinue } }"

call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_pking.log"

start "pking-worker" /MIN cmd /c "cd /d D:\re1_rl && fleet\local\run_distributed_worker_pking_headless_19.cmd >> data\logs\worker_pking.log 2>&1"

echo Started pking headless 19-env worker. Tail: type data\logs\worker_pking.log
