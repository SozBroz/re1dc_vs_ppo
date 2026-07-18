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
    n_actions = 10
    return WorkerRollout(
        worker_id="test-worker",
        policy_version=3,
        n_envs=n_envs,
        n_steps=n_steps,
        obs={
            "frame": np.random.randint(0, 255, (n_steps, n_envs, 84, 77, 4), dtype=np.uint8),
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
        action_masks=np.ones((n_steps, n_envs, n_actions), dtype=np.bool_),
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
    assert np.array_equal(restored.action_masks, original.action_masks)
    # Legacy softlock channel optional; absent → zeros via softlock_rewards().
    assert np.array_equal(
        restored.softlock_rewards(),
        np.zeros_like(original.rewards, dtype=np.float32),
    )


def test_rollout_codec_rejects_missing_masks() -> None:
    """Fail closed: payloads without action_masks must not decode."""
    import io
    import json
    import struct
    import zlib

    from re1_rl.distributed import rollout_codec as rc

    original = _sample_rollout()
    # Build a v2 payload manually without action_masks in npz.
    obs_rest, frame_blob, frame_shape = rc._compress_obs_arrays(original.obs)
    meta = {
        "worker_id": original.worker_id,
        "policy_version": original.policy_version,
        "n_envs": original.n_envs,
        "n_steps": original.n_steps,
        "episode_infos": original.episode_infos,
        "obs_keys": list(obs_rest.keys()),
        "frame_compressed": frame_blob is not None,
        "frame_shape": frame_shape,
    }
    npz = io.BytesIO()
    save_kwargs = {
        "actions": original.actions,
        "rewards": original.rewards,
        "dones": original.dones,
        "values": original.values,
        "log_probs": original.log_probs,
        "last_values": original.last_values,
    }
    for key, arr in obs_rest.items():
        save_kwargs[f"obs__{key}"] = arr
    np.savez_compressed(npz, **save_kwargs)
    meta_bytes = json.dumps(meta).encode("utf-8")
    npz_bytes = npz.getvalue()
    frame_bytes = frame_blob or b""
    blob = (
        rc._MAGIC
        + struct.pack("<BIII", 2, len(meta_bytes), len(npz_bytes), len(frame_bytes))
        + meta_bytes
        + npz_bytes
        + frame_bytes
    )
    import pytest

    with pytest.raises(ValueError, match="action_masks"):
        decode_rollout(blob)


def test_rollout_codec_v2_frame_roundtrip() -> None:
    original = _sample_rollout()
    original.obs["frame"] = np.zeros_like(original.obs["frame"], dtype=np.uint8)
    blob_v2 = encode_rollout(original)
    assert blob_v2[4] == 2  # codec version
    restored = decode_rollout(blob_v2)
    assert np.array_equal(restored.obs["frame"], original.obs["frame"])
