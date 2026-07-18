@echo off
REM pking — monolithic synced SubprocVecEnv (20 envs, visible windows)
setlocal
cd /d D:\re1_rl
set N_ENVS=20
set BASE_PORT=5755

if not exist data\logs mkdir data\logs

echo Killing leftover EmuHawk/python on pking...
taskkill /F /IM EmuHawk.exe >nul 2>&1
for /f "tokens=2" %%p in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST ^| findstr /I "PID:"') do (
  wmic process where "ProcessId=%%p" get CommandLine 2>nul | findstr /I "train_parallel" >nul && taskkill /F /PID %%p >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo Starting pking sync fleet: %N_ENVS% envs ports %BASE_PORT%+
echo Log: data\logs\train_sync_pking_20.log

venv\Scripts\python.exe scripts\train_parallel.py ^
  --sync ^
  --n-envs %N_ENVS% ^
  --base-port %BASE_PORT% ^
  --total-steps 0 ^
  --training-speed 6400 ^
  --skip-chunk 600 ^
  --capture-checkpoints ^
  --resume auto ^
  --no-headless ^
  --screenshot-mmf >> data\logs\train_sync_pking_20.log 2>&1
