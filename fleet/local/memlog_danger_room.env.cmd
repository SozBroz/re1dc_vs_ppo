@echo off
REM Memlog actor: uniform cp00-cp55 resets (matches pking headless worker).
call "%~dp0go_explore_phase_c.env.cmd"
set RE1_YAWN_RESET_FRONTIER_FIGHT_ONLY=
set RE1_YAWN_RESET_PIN_INDEX=
set RE1_YAWN_RESET_PIN_SET=
set RE1_YAWN_RESET_PIN_SET_WEIGHT=
set RE1_YAWN_RESET_PIN_RANGE=0-55
