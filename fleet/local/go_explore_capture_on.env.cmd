@echo off
REM Enable Go-Explore cell capture (canary / post-P1). Source AFTER go_explore_phase_c.env.cmd.
REM Workers stay ephemeral: proposals only; learner writes canonical cells/.
REM Keep RE1_GO_EXPLORE_RESET_WEIGHT=0 until archive resets are validated.
set RE1_GO_EXPLORE_CAPTURE=1
