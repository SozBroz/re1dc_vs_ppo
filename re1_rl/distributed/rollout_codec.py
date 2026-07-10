"""Rollout batch compression for network upload."""

from __future__ import annotations

import json
import struct
import zlib
from io import BytesIO
from typing import Any

import numpy as np

from re1_rl.distributed.rollout_types import WorkerRollout

_MAGIC = b"RE1R"
_VERSION = 2
_FRAME_KEY = "frame"


_FRAME_ZLIB_LEVEL = 1  # fast flush; level 9 blocked actors with marginal size win


def _compress_obs_arrays(obs: dict[str, np.ndarray]) -> tuple[dict[str, np.ndarray], bytes | None, list[int] | None]:
    """Zlib-compress the bulky frame tensor; keep other keys in npz."""
    out = dict(obs)
    frame = out.pop(_FRAME_KEY, None)
    if frame is None:
        return out, None, None
    frame_u8 = np.ascontiguousarray(frame, dtype=np.uint8)
    blob = zlib.compress(frame_u8.tobytes(), level=_FRAME_ZLIB_LEVEL)
    return out, blob, list(frame_u8.shape)


def _decompress_frame(blob: bytes, shape: list[int]) -> np.ndarray:
    raw = zlib.decompress(blob)
    arr = np.frombuffer(raw, dtype=np.uint8)
    return arr.reshape(tuple(shape))


def encode_rollout(rollout: WorkerRollout) -> bytes:
    obs_rest, frame_blob, frame_shape = _compress_obs_arrays(rollout.obs)
    meta: dict[str, Any] = {
        "worker_id": rollout.worker_id,
        "policy_version": rollout.policy_version,
        "n_envs": rollout.n_envs,
        "n_steps": rollout.n_steps,
        "episode_infos": rollout.episode_infos,
        "obs_keys": list(obs_rest.keys()),
        "frame_compressed": frame_blob is not None,
        "frame_shape": frame_shape,
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
    for key, arr in obs_rest.items():
        save_kwargs[f"obs__{key}"] = arr
    np.savez_compressed(npz, **save_kwargs)
    meta_bytes = json.dumps(meta).encode("utf-8")
    npz_bytes = npz.getvalue()
    frame_bytes = frame_blob or b""
    return (
        _MAGIC
        + struct.pack("<BIII", _VERSION, len(meta_bytes), len(npz_bytes), len(frame_bytes))
        + meta_bytes
        + npz_bytes
        + frame_bytes
    )


def decode_rollout(data: bytes) -> WorkerRollout:
    if len(data) < 13 or data[:4] != _MAGIC:
        raise ValueError("invalid rollout payload header")
    version = data[4]
    if version == 1:
        meta_len, npz_len = struct.unpack("<II", data[5:13])
        frame_len = 0
        off = 13
    elif version == 2:
        if len(data) < 17:
            raise ValueError("truncated rollout v2 header")
        meta_len, npz_len, frame_len = struct.unpack("<III", data[5:17])
        off = 17
    else:
        raise ValueError(f"unsupported rollout codec version {version}")
    meta = json.loads(data[off : off + meta_len].decode("utf-8"))
    off += meta_len
    npz_bytes = data[off : off + npz_len]
    off += npz_len
    if version == 2:
        frame_bytes = data[off : off + frame_len]
        if len(frame_bytes) != frame_len:
            raise ValueError("truncated rollout frame blob")
    else:
        frame_bytes = b""

    if len(npz_bytes) != npz_len:
        raise ValueError("truncated rollout payload")

    with np.load(BytesIO(npz_bytes), allow_pickle=False) as loaded:
        obs: dict[str, np.ndarray] = {}
        for key in meta["obs_keys"]:
            obs[key] = loaded[f"obs__{key}"]
        if meta.get("frame_compressed") and frame_bytes:
            shape = meta.get("frame_shape")
            if not shape:
                raise ValueError("missing frame_shape in rollout meta")
            obs[_FRAME_KEY] = _decompress_frame(frame_bytes, list(shape))
        elif _FRAME_KEY in meta.get("obs_keys", []):
            obs[_FRAME_KEY] = loaded[f"obs__{_FRAME_KEY}"]
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
