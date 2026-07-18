@echo off
cd /d D:\re1_rl
echo === WH1 PRIME CHECK ===
git log -1 --oneline
if exist tools\BizHawk-2.11.1\EmuHawk.exe (echo BIZHAWK_OK) else (echo NO_BIZHAWK)
if exist roms (echo ROMS_OK) else (echo NO_ROMS)
if exist states\jill_control_fresh.State (echo STATE_OK) else (echo NO_STATE)
if exist venv\Scripts\python.exe (echo VENV_OK) else (echo NO_VENV)
echo --- sessions ---
query user
echo --- free RAM ---
powershell -NoProfile -Command " $o=Get-CimInstance Win32_OperatingSystem; '{0:N1} / {1:N1} GB free/total' -f ($o.FreePhysicalMemory/1MB),($o.TotalVisibleMemorySize/1MB)"
echo --- learner health ---
call "%~dp0..\fleet_hosts.cmd"
venv\Scripts\python.exe -c "from re1_rl.distributed.worker_client import WorkerClient; import os; h=os.environ.get('FLEET_LEARNER_HOST','192.168.0.116'); c=WorkerClient(h,8765,machine_name='probe',timeout=5); print('health', c.health())"
echo --- session note ---
echo If STATE=Disc above, RDP into WH1 before starting the worker.
echo --- leftover processes ---
tasklist /FI "IMAGENAME eq EmuHawk.exe"
tasklist /FI "IMAGENAME eq python.exe"
echo --- ports 5655-5662 ---
netstat -ano | findstr ":565"
echo === DONE ===
