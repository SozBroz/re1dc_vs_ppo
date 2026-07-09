"""Rollout codec roundtrip."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.rollout_codec import decode_rollout, encode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.obs_encoder import BOX_DIM, GOAL_DIM, PROPRIO_DIM


def _sample_rollout() -> WorkerRollout:
    n_steps, n_envs = 4, 2
    return WorkerRollout(
        worker_id="test-worker",
        policy_version=3,
        n_envs=n_envs,
        n_steps=n_steps,
        obs={
            "frame": np.random.randint(0, 255, (n_steps, n_envs, 84, 84, 4), dtype=np.uint8),
            "proprio": np.random.randn(n_steps, n_envs, PROPRIO_DIM).astype(np.float32),
            "goal": np.random.randn(n_steps, n_envs, GOAL_DIM).astype(np.float32),
            "spatial": np.random.randn(n_steps, n_envs, 119).astype(np.float32),
            "visited": np.random.rand(n_steps, n_envs, 16, 16, 1).astype(np.float32),
            "box": np.random.randn(n_steps, n_envs, BOX_DIM).astype(np.float32),
        },
        actions=np.random.randint(0, 10, (n_steps, n_envs), dtype=np.int64),
        rewards=np.random.randn(n_steps, n_envs).astype(np.float32),
        dones=np.zeros((n_steps, n_envs), dtype=np.bool_),
        values=np.random.randn(n_steps, n_envs).astype(np.float32),
        log_probs=np.random.randn(n_steps, n_envs).astype(np.float32),
        last_values=np.random.randn(n_envs).astype(np.float32),
        episode_infos=[{"room_id": "101"}],
    )


def test_rollout_codec_roundtrip() -> None:
    original = _sample_rollout()
    blob = encode_rollout(original)
    restored = decode_rollout(blob)
    assert restored.worker_id == original.worker_id
    assert restored.policy_version == original.policy_version
    assert restored.n_envs == original.n_envs
    assert restored.n_steps == original.n_steps
    for key in original.obs:
        assert np.array_equal(restored.obs[key], original.obs[key])
    assert np.array_equal(restored.actions, original.actions)
    assert np.array_equal(restored.last_values, original.last_values)
