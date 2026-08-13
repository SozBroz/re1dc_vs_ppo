@echo off
REM Memlog actor: same hot-reload pin as pking workers (data\yawn_reset_pin.env).
call "%~dp0go_explore_phase_c.env.cmd"
set RE1_YAWN_RESET_FRONTIER_FIGHT_ONLY=
set RE1_YAWN_RESET_PIN_FILE=data\yawn_reset_pin.env
set RE1_YAWN_RESET_PIN_INDEX=
set RE1_YAWN_RESET_PIN_SET=
set RE1_YAWN_RESET_PIN_SET_WEIGHT=
set RE1_YAWN_RESET_PIN_RANGE=65-100
