"""Chosen action + legal mask + odds; stays out of replay-only loads."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from re1_rl.footage_trace import (
    FootageTraceBuffer,
    estimate_policy_bytes,
    maybe_write_footage_trace,
    new_footage_trace_buffer,
)
from re1_rl.go_explore_merge import CELL_POLICY_NAME
from re1_rl.leg_replay import LegReplayBuffer


def test_buffer_roundtrip(tmp_path) -> None:
    buf = new_footage_trace_buffer()
    mask = np.zeros(45, dtype=bool)
    mask[[1, 5, 7]] = True
    probs = np.zeros(45, dtype=np.float32)
    probs[1] = 0.2
    probs[5] = 0.1
    probs[7] = 0.7
    buf.append(action=7, action_mask=mask, masked_probs=probs, policy_version=12)
    dest = buf.write(tmp_path / CELL_POLICY_NAME)
    loaded = np.load(dest)
    assert int(loaded["action"][0]) == 7
    assert bool(loaded["action_mask"][0, 7])
    assert not bool(loaded["action_mask"][0, 0])
    assert abs(float(loaded["masked_probs"][0, 7]) - 0.7) < 1e-6
    assert int(loaded["policy_version"]) == 12
    assert dest.stat().st_size < 8_000


def test_write_gated_to_staging(tmp_path) -> None:
    buf = FootageTraceBuffer()
    buf.append(
        action=1,
        action_mask=np.ones(45, dtype=bool),
        masked_probs=np.full(45, 1.0 / 45.0, dtype=np.float32),
    )
    replay = LegReplayBuffer()
    replay.append(1, 8)
    env = SimpleNamespace(
        _route_start_index=18,
        _leg_replay=replay,
        _footage_trace=buf,
    )
    dest = maybe_write_footage_trace(env, tmp_path, completed_index=18)
    assert dest == tmp_path / CELL_POLICY_NAME
    loaded = np.load(dest)
    assert int(loaded["action"][0]) == 1
    env._route_start_index = 10
    assert maybe_write_footage_trace(env, tmp_path, completed_index=18) is None


def test_policy_bytes_are_small() -> None:
    assert estimate_policy_bytes(225) < 60_000
    assert estimate_policy_bytes(2300) < 600_000
    assert estimate_policy_bytes(21404) < 6_000_000
