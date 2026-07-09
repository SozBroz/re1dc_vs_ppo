"""Rollout batch compression for network upload."""

from __future__ import annotations

import json
import struct
from io import BytesIO
from typing import Any

import numpy as np

from re1_rl.distributed.rollout_types import WorkerRollout

_MAGIC = b"RE1R"
_VERSION = 1


def encode_rollout(rollout: WorkerRollout) -> bytes:
    meta: dict[str, Any] = {
        "worker_id": rollout.worker_id,
        "policy_version": rollout.policy_version,
        "n_envs": rollout.n_envs,
        "n_steps": rollout.n_steps,
        "episode_infos": rollout.episode_infos,
        "obs_keys": list(rollout.obs.keys()),
    }
    npz = BytesIO()
    save_kwargs: dict[str, np.ndarray] = {
        "actions": rollout.actions,
        "rewards": rollout.rewards,
        "dones": rollout.dones,
        "values": rollout.values,
        "log_probs": rollout.log_probs,
        "last_values": rollout.last_values,
    }
    for key, arr in rollout.obs.items():
        save_kwargs[f"obs__{key}"] = arr
    np.savez_compressed(npz, **save_kwargs)
    meta_bytes = json.dumps(meta).encode("utf-8")
    npz_bytes = npz.getvalue()
    return (
        _MAGIC
        + struct.pack("<BII", _VERSION, len(meta_bytes), len(npz_bytes))
        + meta_bytes
        + npz_bytes
    )


def decode_rollout(data: bytes) -> WorkerRollout:
    if len(data) < 13 or data[:4] != _MAGIC:
        raise ValueError("invalid rollout payload header")
    version, meta_len, npz_len = struct.unpack("<BII", data[4:13])
    if version != _VERSION:
        raise ValueError(f"unsupported rollout codec version {version}")
    off = 13
    meta = json.loads(data[off : off + meta_len].decode("utf-8"))
    off += meta_len
    npz_bytes = data[off : off + npz_len]
    if len(npz_bytes) != npz_len:
        raise ValueError("truncated rollout payload")
    with np.load(BytesIO(npz_bytes), allow_pickle=False) as loaded:
        obs: dict[str, np.ndarray] = {}
        for key in meta["obs_keys"]:
            obs[key] = loaded[f"obs__{key}"]
        return WorkerRollout(
            worker_id=str(meta["worker_id"]),
            policy_version=int(meta["policy_version"]),
            n_envs=int(meta["n_envs"]),
            n_steps=int(meta["n_steps"]),
            obs=obs,
            actions=loaded["actions"],
            rewards=loaded["rewards"],
            dones=loaded["dones"],
            values=loaded["values"],
            log_probs=loaded["log_probs"],
            last_values=loaded["last_values"],
            episode_infos=list(meta.get("episode_infos") or []),
        )
