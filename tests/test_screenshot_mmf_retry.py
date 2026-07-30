"""screenshot_mmf retries transient failures instead of latching disabled."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient


def test_screenshot_mmf_retries_then_succeeds() -> None:
    client = BizHawkClient(port=5999, screenshot_mmf=True)
    client._screenshot_mmf_retries = 3
    client._screenshot_mmf_retry_sleep_s = 0.0
    rgb = np.zeros((8, 8, 3), dtype=np.uint8)
    calls = {"n": 0}

    def flaky() -> np.ndarray:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("NLua transient")
        return rgb

    with patch.object(client, "_screenshot_from_mmf", side_effect=flaky):
        out = client.screenshot()
    assert out is rgb
    assert calls["n"] == 3


def test_screenshot_mmf_raises_after_retries_exhausted() -> None:
    client = BizHawkClient(port=5999, screenshot_mmf=True)
    client._screenshot_mmf_retries = 3
    client._screenshot_mmf_retry_sleep_s = 0.0

    with patch.object(
        client, "_screenshot_from_mmf", side_effect=RuntimeError("still broken")
    ):
        with pytest.raises(RuntimeError, match="after 3 attempt"):
            client.screenshot()
