"""Action-space wiring tests (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES


def test_interact_maps_to_cross() -> None:
    idx = ACTION_NAMES.index("interact")
    assert ACTION_BUTTON_MAP[idx] == {"cross": True}


def test_knife_swing_action_index() -> None:
    idx = ACTION_NAMES.index("knife_swing")
    assert idx == 8
    assert ACTION_BUTTON_MAP[idx] == {"r1": True, "down": True, "cross": True}
