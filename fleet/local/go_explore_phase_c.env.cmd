@echo off
REM Progress-gated Go-Explore: capture ON, 30/70 fresh/archive reset mix, hard disk caps.
call "%~dp0obs_raw.env.cmd"
call "%~dp0reset_mix_l_passage.env.cmd"
set RE1_GO_EXPLORE_CAPTURE=1
set RE1_GO_EXPLORE_ARCHIVE=data\go_explore\archive.json
REM Yawn rails: learner-authoritative cell sync (proposals → ingest → worker poll).
REM Go-Explore mirror stays off.
set RE1_GO_EXPLORE_SYNC=0
set RE1_YAWN_RAILS_SYNC=1
set RE1_YAWN_RAILS_ROOT=states\yawn_rails
set RE1_YAWN_RAILS_MANIFEST_POLL_S=60
REM Pay-forward: 20% latest + 80% equal fight budgets; ripple improved loadouts
REM through each stretch until the next fighting CP (or a blocked hop).
set RE1_YAWN_PAYFORWARD_RIPPLE=1
set RE1_YAWN_PAYFORWARD_FORCE_FIGHTS=45
set RE1_YAWN_PAYFORWARD_IGNORE_FIGHTS=52
set RE1_GO_EXPLORE_MANIFEST_POLL_S=60
REM Learner HTTP for lazy bundle fetch in worker subprocesses (archive resets).
if not defined RE1_LEARNER_HOST set RE1_LEARNER_HOST=%FLEET_LEARNER_HOST%
if not defined RE1_LEARNER_PORT set RE1_LEARNER_PORT=%FLEET_LEARNER_PORT%
REM Disk guards — progress-only capture; keep daily growth bounded.
set RE1_GO_CAPTURE_COOLDOWN_STEPS=600
set RE1_GO_MIN_FREE_GB=100
set RE1_GO_MAX_CAPTURES_DAY=50
set RE1_GO_MAX_CAPTURE_BYTES_DAY=100000000
set RE1_GO_REPLACE_BUDGET_DAY=50
set RE1_GO_REPLACE_MIN_HP_DELTA=1
set RE1_GO_REPLACE_MIN_AMMO_DELTA=1
REM Room-first archive: one cell per (room, milestone_digest); pose cap per bucket.
set RE1_GO_MAX_POSES_PER_BUCKET=1
set RE1_GO_MAX_ARCHIVE_CELLS=800
set RE1_GO_POSE_EVICT=1
set RE1_GO_MAX_CELLS_PER_ROOM=32
