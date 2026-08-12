"""BizHawkClient.step wire-format guards (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient


def test_pulse_hold_without_sticky_uses_sticky_wire() -> None:
    client = BizHawkClient(port=5990)
    client._client = MagicMock()
    captured: list[dict] = []

    def _capture(cmd: dict) -> dict:
        captured.append(cmd)
        return {"ok": True, "frame": 1}

    client._request = _capture  # type: ignore[method-assign]
    client.step(pulse_hold={"cross": True}, n=3, abort_on_zero_hp=False)

    assert len(captured) == 1
    req = captured[0]
    assert req["cmd"] == "step"
    assert req["sticky"] == {}
    assert req["pulse_hold"] == {"cross": True}
    assert "buttons" not in req


def test_legacy_buttons_when_no_pulse() -> None:
    client = BizHawkClient(port=5991)
    client._client = MagicMock()
    captured: list[dict] = []

    def _capture(cmd: dict) -> dict:
        captured.append(cmd)
        return {"ok": True, "frame": 1}

    client._request = _capture  # type: ignore[method-assign]
    client.step(buttons={"cross": True}, n=2)

    req = captured[0]
    assert req["buttons"] == {"cross": True}
    assert "sticky" not in req
