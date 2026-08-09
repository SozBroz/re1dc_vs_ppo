"""Episode-boundary input flush (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES
from re1_rl.sticky_input import StickyInputState


def test_clear_latched_input_sends_clear_input_cmd() -> None:
    client = BizHawkClient(port=5988)
    seen: list[dict] = []

    def fake_request(req: dict) -> dict:
        seen.append(dict(req))
        return {"ok": True}

    client._request = fake_request  # type: ignore[method-assign]
    client.clear_latched_input()
    assert seen == [{"cmd": "clear_input"}]


def test_sticky_reset_clears_python_latch_before_new_action() -> None:
    s = StickyInputState()
    s.apply(ACTION_NAMES.index("run_forward"), ACTION_BUTTON_MAP)
    assert any(s.as_dict().values())
    s.reset()
    assert s.as_dict() == {
        "up": False,
        "down": False,
        "left": False,
        "right": False,
        "square": False,
    }
