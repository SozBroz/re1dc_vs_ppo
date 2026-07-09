"""Async distributed worker helpers (no BizHawk)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.async_worker_runtime import (
    _serve_need,
    worker_rollout_from_actor_msg,
)
from re1_rl.env import ACTION_NAMES

N_ACTIONS = len(ACTION_NAMES)


class _FakePolicy:
    def __init__(self) -> None:
        self.policy_version = 7
        self.masked_calls = 0
        self.batch_calls = 0

    def predict_masked(self, obs, masks):
        self.masked_calls += 1
        assert masks.dtype == bool
        assert masks.shape[-1] == N_ACTIONS
        return 3, 0.5, -0.1

    def predict_batch(self, obs):
        self.batch_calls += 1
        return np.array([1]), np.array([0.2], dtype=np.float32), np.array([-0.2], dtype=np.float32)

    def predict_values(self, obs):
        return np.array([1.25], dtype=np.float32)


def _fake_obs() -> dict[str, np.ndarray]:
    return {
        "frame": np.zeros((84, 84, 4), dtype=np.uint8),
        "proprio": np.zeros((8,), dtype=np.float32),
    }


def test_serve_need_uses_predict_masked_when_masks_present() -> None:
    policy = _FakePolicy()
    conn = MagicMock()
    masks = np.ones(N_ACTIONS, dtype=bool)
    masks[0] = False
    _serve_need(conn, {"t": "need", "obs": _fake_obs(), "action_masks": masks}, policy)
    assert policy.masked_calls == 1
    assert policy.batch_calls == 0
    conn.send.assert_called_once()
    payload = conn.send.call_args[0][0]
    assert payload["t"] == "act"
    assert payload["action"] == 3
    assert payload["value"] == 0.5
    assert payload["logprob"] == -0.1


def test_serve_need_falls_back_to_predict_batch() -> None:
    policy = _FakePolicy()
    conn = MagicMock()
    _serve_need(conn, {"t": "need", "obs": _fake_obs()}, policy)
    assert policy.batch_calls == 1
    assert policy.masked_calls == 0
    payload = conn.send.call_args[0][0]
    assert payload["action"] == 1


def test_worker_rollout_from_actor_msg_shapes() -> None:
    policy = _FakePolicy()
    n_steps = 4
    msg: dict[str, Any] = {
        "t": "rollout",
        "rank": 2,
        "obs": {
            "frame": np.zeros((n_steps, 84, 84, 4), dtype=np.uint8),
            "proprio": np.zeros((n_steps, 8), dtype=np.float32),
        },
        "actions": np.arange(n_steps, dtype=np.int64),
        "rewards": np.ones(n_steps, dtype=np.float32),
        "dones": np.zeros(n_steps, dtype=np.bool_),
        "values": np.full(n_steps, 0.3, dtype=np.float32),
        "log_probs": np.full(n_steps, -0.4, dtype=np.float32),
        "last_obs": _fake_obs(),
        "episode_infos": [{"room_id": "104"}],
    }
    rollout = worker_rollout_from_actor_msg(
        msg, policy=policy, worker_id="pking", n_steps=n_steps
    )
    assert rollout.worker_id == "pking:actor_2"
    assert rollout.policy_version == 7
    assert rollout.n_envs == 1
    assert rollout.n_steps == n_steps
    assert rollout.num_timesteps() == n_steps
    assert rollout.actions.shape == (n_steps, 1)
    assert rollout.rewards.shape == (n_steps, 1)
    assert rollout.obs["frame"].shape == (n_steps, 1, 84, 84, 4)
    assert rollout.last_values.shape == (1,)
    assert rollout.episode_infos == [{"room_id": "104"}]
