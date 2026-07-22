@echo off
REM Human validation harness for the Main Hall typewriter PB champion.
REM Usage:
REM   scripts\validate_typewriter_champion.cmd
REM   scripts\validate_typewriter_champion.cmd path\to\champion.State
setlocal
cd /d D:\re1_rl

set STATE=%~1
if "%STATE%"=="" set STATE=states\pb\champions\mainhall_typewriter\champion.State
set SIDE=%~dpn1.sidecar.json
if "%~1"=="" set SIDE=states\pb\champions\mainhall_typewriter\champion.sidecar.json

if not exist "%STATE%" (
  echo ERROR: missing savestate: %STATE%
  exit /b 1
)

echo === Typewriter PB validate ===
echo State:   %STATE%
if exist "%SIDE%" (
  echo Sidecar: %SIDE%
  echo play_human will apply sibling .sidecar.json ^(anti-repay visited/cutscenes^)
) else (
  echo Sidecar: ^(missing^) — State only; room rewards will re-pay like a fresh episode
)
echo Controls: play_human on port 7788, --no-training-parity
echo.

venv\Scripts\python.exe scripts\play_human.py ^
  --no-training-parity ^
  --start-savestate "%STATE%" ^
  --port 7788 ^
  --cutscene-gate-log ^
  --non-step-rewards
