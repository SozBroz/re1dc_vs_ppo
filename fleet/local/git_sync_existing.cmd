@echo off
REM One-time: attach git to an existing tree (keeps data/, roms/, venv/ untracked).
cd /d %~1
if exist .git goto fetch
git init
git remote add origin https://github.com/SozBroz/re1dc_vs_ppo.git
:fetch
git fetch origin master
git reset --hard origin/master
echo SYNCED %CD% at
git rev-parse --short HEAD
