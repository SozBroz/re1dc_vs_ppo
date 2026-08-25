@echo off
REM Planner-loyal fleet: tip pl05 → shield key; thin cells; no yawn/GO capture noise.
call "%~dp0obs_raw.env.cmd"
set RE1_PLANNER_LOYAL=1
if "%RE1_PLANNER_CHUNK%"=="" set RE1_PLANNER_CHUNK=data\planner_chunks\cp05_shield_key.json
set RE1_PLANNER_LOYAL_CELLS_ROOT=states\planner_loyal
REM Thin status cells only (State+sidecar+meta+quality). No PPO leg tapes.
set RE1_YAWN_LEG_REPLAY=0
set RE1_YAWN_PAYFORWARD_RIPPLE=0
set RE1_YAWN_EXTEND_EPISODE_ON_CELL=0
set RE1_YAWN_RAILS_SYNC=0
set RE1_GO_EXPLORE_CAPTURE=0
set RE1_GO_EXPLORE_SYNC=0
set RE1_PB_CAPTURE=1
set RE1_PB_V1_TYPEWRITER_ONLY=1
set RE1_PB_DANGER_ROOMS=1
if not defined RE1_LEARNER_HOST set RE1_LEARNER_HOST=%FLEET_LEARNER_HOST%
if not defined RE1_LEARNER_PORT set RE1_LEARNER_PORT=%FLEET_LEARNER_PORT%
