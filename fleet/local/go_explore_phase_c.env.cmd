@echo off
REM Phase C — shadow Go-Explore: capture OFF until disk retention fixed (see docs/go_explore_disk_efficiency.plan.md).
REM Archive resets still off (RE1_GO_EXPLORE_RESET_WEIGHT=0); PB sidecars unchanged.
set RE1_GO_EXPLORE_CAPTURE=0
set RE1_GO_EXPLORE_RESET_WEIGHT=0
set RE1_GO_EXPLORE_ARCHIVE=data\go_explore\archive.json
set RE1_GO_EXPLORE_MANIFEST_POLL_S=60
REM Disk guards (steady-state << 1 GB/day per machine).
set RE1_GO_CAPTURE_COOLDOWN_STEPS=60
set RE1_GO_MIN_FREE_GB=100
set RE1_GO_MAX_CAPTURES_DAY=200
set RE1_GO_MAX_CAPTURE_BYTES_DAY=250000000
set RE1_GO_REPLACE_BUDGET_DAY=200
set RE1_GO_REPLACE_MIN_HP_DELTA=5
set RE1_GO_REPLACE_MIN_AMMO_DELTA=10
REM Semantic admission (pose cap per room+digest; hard archive ceiling).
set RE1_GO_MAX_POSES_PER_BUCKET=6
set RE1_GO_MAX_ARCHIVE_CELLS=8000
set RE1_GO_POSE_EVICT=1
set RE1_GO_MAX_CELLS_PER_ROOM=20
