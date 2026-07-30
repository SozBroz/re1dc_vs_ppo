"""Step-integrated MMF capture (no Lua PNG base64 on training steps)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient


def test_step_capture_final_mmf_uses_inline_tag() -> None:
    client = BizHawkClient(port=5998, screenshot_mmf=True)

    def fake_request(req: dict) -> dict:
        assert req["cmd"] == "step"
        assert req.get("capture_final_mmf") is True
        assert req.get("mmf_name") == client.mmf_name
        return {
            "ok": True,
            "frame": 42,
            "final_mmf_name": client.mmf_name,
            "final_mmf_size": 128,
            "final_mmf_frame": 42,
        }

    client._request = fake_request  # type: ignore[method-assign]

    with patch.object(
        client,
        "_read_mmf_png",
        return_value=np.zeros((240, 350, 3), dtype=np.uint8),
    ):
        with patch.object(client, "capture_final_ring_frame") as fallback:
            frame, died = client.step(n=8, sticky={}, capture_final=True)

    assert frame == 42
    assert died is False
    fallback.assert_not_called()
    assert client.build_frame_stack().shape == (63, 84, 4)


def test_step_capture_final_mmf_falls_back_on_missing_tag() -> None:
    client = BizHawkClient(port=5997, screenshot_mmf=True)
    client._request = lambda _req: {"ok": True, "frame": 7}  # type: ignore[method-assign]

    with patch.object(client, "capture_final_ring_frame") as fallback:
        client.step(n=4, sticky={}, capture_final=True)

    fallback.assert_called_once()


def test_step_skips_inline_mmf_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BizHawkClient(port=5996, screenshot_mmf=False)
    seen: list[dict] = []

    def fake_request(req: dict) -> dict:
        seen.append(dict(req))
        return {"ok": True, "frame": 1}

    client._request = fake_request  # type: ignore[method-assign]

    with patch.object(client, "capture_final_ring_frame") as fallback:
        client.step(n=2, sticky={}, capture_final=True)

    assert "capture_final_mmf" not in seen[0]
    fallback.assert_called_once()
