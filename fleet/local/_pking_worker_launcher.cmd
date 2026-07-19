@echo off
cd /d D:\re1_rl
call "D:\re1_rl\fleet\local\flush_log.cmd" "D:\re1_rl\data\logs\worker_pking.log"
call "D:\re1_rl\fleet\local\run_distributed_worker_pking.cmd" >> data\logs\worker_pking.log 2>&1
