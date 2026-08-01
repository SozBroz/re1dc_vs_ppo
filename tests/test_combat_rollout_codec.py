"""Rollout codec round-trip with combat/world aux targets."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.combat_targets import COMBAT_TARGET_DIM, WORLD_EVENT_DIM, empty_combat_target
from re1_rl.distributed.rollout_codec import decode_rollout, encode_rollout
from re1_rl.distributed.rollout_types import WorkerRollout


def test_codec_roundtrip_with_aux_targets() -> None:
    n_steps, n_envs, n_actions = 4, 2, 45
    combat = np.zeros((n_steps, n_envs, COMBAT_TARGET_DIM), dtype=np.float32)
    combat[0, 0] = empty_combat_target()
    combat[1, 0] = np.array([0, 1, 0, 0.1, 0, 0.01, 0, 0, 1], dtype=np.float32)
    world = np.zeros((n_steps, n_envs, WORLD_EVENT_DIM), dtype=np.float32)
    wmask = np.zeros((n_steps, n_envs, WORLD_EVENT_DIM), dtype=np.float32)
    wmask[..., :10] = 1.0
    rollout = WorkerRollout(
        worker_id="w0",
        policy_version=3,
        n_envs=n_envs,
        n_steps=n_steps,
        obs={
            "proprio": np.zeros((n_steps, n_envs, 28), dtype=np.float32),
            "frame": np.zeros((n_steps, n_envs, 3, 84, 112), dtype=np.uint8),
        },
        actions=np.zeros((n_steps, n_envs), dtype=np.int64),
        rewards=np.zeros((n_steps, n_envs), dtype=np.float32),
        dones=np.zeros((n_steps, n_envs), dtype=np.bool_),
        values=np.zeros((n_steps, n_envs), dtype=np.float32),
        log_probs=np.zeros((n_steps, n_envs), dtype=np.float32),
        last_values=np.zeros((n_envs,), dtype=np.float32),
        action_masks=np.ones((n_steps, n_envs, n_actions), dtype=np.bool_),
        combat_targets=combat,
        world_event_targets=world,
        world_event_masks=wmask,
    )
    decoded = decode_rollout(encode_rollout(rollout))
    assert decoded.combat_targets is not None
    assert np.allclose(decoded.combat_targets[1, 0], combat[1, 0])
    assert decoded.world_event_masks is not None
    assert decoded.world_event_masks[0, 0, 0] == 1.0
