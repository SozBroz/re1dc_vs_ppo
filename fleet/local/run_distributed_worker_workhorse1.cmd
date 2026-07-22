@echo off
REM workhorse1 remote worker (192.168.0.203) — interactive desktop required for EmuHawk.
setlocal
cd /d D:\re1_rl
call "%~dp0..\fleet_hosts.cmd"
set MACHINE_NAME=workhorse1
set LEARNER_HOST=%FLEET_LEARNER_HOST%
set BASE_PORT=5655
set N_ENVS=8
set SYNC_INTERVAL_S=360

REM Typewriter PB champion — local capture; mix via PbChampionResetWrapper.
REM Reset mix: sample_typewriter_start (N=0 fresh only; N=1 50/50; N>=2 fresh 1/3).
set RE1_PB_CAPTURE=1
set RE1_PB_V1_TYPEWRITER_ONLY=1

if not exist data\logs mkdir data\logs

echo Killing leftover EmuHawk/python on WH1...
taskkill /F /IM EmuHawk.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

REM Drop wedged PB sync locks before worker comes up.
powershell -NoProfile -Command ^
  "$root='D:\re1_rl\states\pb\champions'; if (Test-Path $root) { Get-ChildItem $root -Directory -EA SilentlyContinue | ForEach-Object { Remove-Item -Force (Join-Path $_.FullName 'champion.sync.lock') -EA SilentlyContinue; Remove-Item -Recurse -Force (Join-Path $_.FullName '.incoming') -EA SilentlyContinue } }"

REM Fresh heuristics log for this batch (truncate; do not delete).
call "%~dp0flush_log.cmd" "D:\re1_rl\data\logs\worker_workhorse1.log"

echo Starting WH1 worker: %N_ENVS% envs ports %BASE_PORT%+ -> learner %LEARNER_HOST%:8765
echo Log: data\logs\worker_workhorse1.log
call fleet\local\run_distributed_worker.cmd >> data\logs\worker_workhorse1.log 2>&1
