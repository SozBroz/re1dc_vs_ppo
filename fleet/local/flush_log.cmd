@echo off
REM Truncate a log file in place (keep the path; do not delete).
REM Used on batch restart so fleet\local\tail_training_heuristics.py
REM only sees the current process lifetime.
if "%~1"=="" exit /b 1
for %%I in ("%~1") do if not exist "%%~dpI" mkdir "%%~dpI" >nul 2>&1
type nul > "%~1"
exit /b 0
