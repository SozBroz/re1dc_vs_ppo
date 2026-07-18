"""BizHawk human reward monitor — movement + X/Cross at agent cadence.

Chunks each press for ``--frame-skip`` emulated frames (default 8, same as
``RE1Env`` / PPO). Prints **non-step** reward channels with a RAM pose line.
RAM screen log also prints when session bytes change.

Controls (only these are accepted):
  Keyboard: WASD/arrows move | Shift run | X/Z/E = Cross (interact)
  Pad: stick/D-pad | Square run | Cross/X interact

Usage:
  python scripts/monitor_human_rewards.py
  python scripts/monitor_human_rewards.py --start-savestate path/to.State
  python scripts/monitor_human_rewards.py --no-training-parity --port 7790
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAY = ROOT / "scripts" / "play_human.py"

# Defaults first; CLI after so the operator can override.
_DEFAULTS = [
    "--quiet",
    "--deafen-step",
    "--non-step-rewards",
    "--move-cross-only",
    "--frame-skip",
    "8",
    "--input",
    "both",
]


def main() -> int:
    sys.argv = [str(PLAY), *_DEFAULTS, *sys.argv[1:]]
    print(
        "[monitor] movement+Cross only | frame_skip=8 agent cadence | "
        "non-step rewards + RAM (step contempt deafened)",
        flush=True,
    )
    runpy.run_path(str(PLAY), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
