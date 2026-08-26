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
_VERSION = 3  # + optional combat/world aux targets
_FRAME_KEY = "frame"


_FRAME_ZLIB_LEVEL = 1  # fast flush; level 9 blocked actors with marginal size win


def _compress_obs_arrays(obs: dict[str, np.ndarray]) -> tuple[dict[str, np.ndarray], bytes | None, list[int] | None]:
    """Zlib-compress the bulky frame tensor; keep other keys in npz."""
    out = dict(obs)
    frame = out.pop(_FRAME_KEY, None)
    if frame is None:
        return out, None, None
    frame_u8 = np.ascontiguousarray(frame, dtype=np.uint8)
    blob = zlib.compress(memoryview(frame_u8), level=_FRAME_ZLIB_LEVEL)
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
        "has_action_masks": True,
        "has_combat_targets": rollout.combat_targets is not None,
        "has_world_event_targets": rollout.world_event_targets is not None,
        "has_mod_drop_masks": rollout.mod_drop_masks is not None,
        "curriculum_id": str(rollout.curriculum_id or ""),
        "obs_schema_version": int(rollout.obs_schema_version or 0),
    }
    npz = BytesIO()
    save_kwargs: dict[str, np.ndarray] = {
        "actions": rollout.actions,
        "rewards": rollout.rewards,
        "dones": rollout.dones,
        "values": rollout.values,
        "log_probs": rollout.log_probs,
        "last_values": rollout.last_values,
        "action_masks": np.asarray(rollout.action_masks, dtype=np.bool_),
        "rewards_softlock": rollout.softlock_rewards(),
    }
    if rollout.combat_targets is not None:
        save_kwargs["combat_targets"] = np.asarray(rollout.combat_targets, dtype=np.float32)
    if rollout.world_event_targets is not None:
        save_kwargs["world_event_targets"] = np.asarray(
            rollout.world_event_targets, dtype=np.float32
        )
    if rollout.world_event_masks is not None:
        save_kwargs["world_event_masks"] = np.asarray(
            rollout.world_event_masks, dtype=np.float32
        )
    if rollout.mod_drop_masks is not None:
        save_kwargs["mod_drop_masks"] = np.asarray(
            rollout.mod_drop_masks, dtype=np.float32
        )
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


def _parse_rollout_header(data: bytes) -> tuple[dict[str, Any], int, int, int, int]:
    """Return (meta, version, npz_off, npz_len, frame_len) without decoding arrays."""
    if len(data) < 13 or data[:4] != _MAGIC:
        raise ValueError("invalid rollout payload header")
    version = data[4]
    if version == 1:
        meta_len, npz_len = struct.unpack("<II", data[5:13])
        frame_len = 0
        off = 13
    elif version in (2, 3):
        if len(data) < 17:
            raise ValueError("truncated rollout v2/v3 header")
        meta_len, npz_len, frame_len = struct.unpack("<III", data[5:17])
        off = 17
    else:
        raise ValueError(f"unsupported rollout codec version {version}")
    meta_end = off + meta_len
    if len(data) < meta_end:
        raise ValueError("truncated rollout meta")
    meta = json.loads(data[off:meta_end].decode("utf-8"))
    npz_off = meta_end
    return meta, int(version), npz_off, int(npz_len), int(frame_len)


def peek_rollout_timesteps(data: bytes) -> int:
    """Env-step count from the wire header only (no npz/frame decode)."""
    meta, _version, _npz_off, npz_len, frame_len = _parse_rollout_header(data)
    need = _npz_off + npz_len + frame_len
    if len(data) < need:
        raise ValueError("truncated rollout payload")
    n_steps = int(meta.get("n_steps") or 0)
    n_envs = int(meta.get("n_envs") or 0)
    if n_steps <= 0 or n_envs <= 0:
        raise ValueError("invalid rollout shape in meta")
    return n_steps * n_envs


def decode_rollout(data: bytes) -> WorkerRollout:
    meta, version, off, npz_len, frame_len = _parse_rollout_header(data)
    off = off  # npz offset
    npz_bytes = data[off : off + npz_len]
    off += npz_len
    if version in (2, 3):
        frame_bytes = data[off : off + frame_len]
        if len(frame_bytes) != frame_len:
            raise ValueError("truncated rollout frame blob")
    else:
        frame_bytes = b""

    if len(npz_bytes) != npz_len:
        raise ValueError("truncated rollout payload")

    with np.load(BytesIO(npz_bytes), allow_pickle=False) as loaded:
        if "action_masks" not in loaded.files:
            raise ValueError(
                "rollout missing action_masks (fail closed — upgrade workers)"
            )
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
        softlock = (
            loaded["rewards_softlock"]
            if "rewards_softlock" in loaded.files
            else np.zeros_like(loaded["rewards"], dtype=np.float32)
        )
        combat_targets = (
            np.asarray(loaded["combat_targets"], dtype=np.float32)
            if "combat_targets" in loaded.files
            else None
        )
        world_event_targets = (
            np.asarray(loaded["world_event_targets"], dtype=np.float32)
            if "world_event_targets" in loaded.files
            else None
        )
        world_event_masks = (
            np.asarray(loaded["world_event_masks"], dtype=np.float32)
            if "world_event_masks" in loaded.files
            else None
        )
        mod_drop_masks = (
            np.asarray(loaded["mod_drop_masks"], dtype=np.float32)
            if "mod_drop_masks" in loaded.files
            else None
        )
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
            action_masks=np.asarray(loaded["action_masks"], dtype=np.bool_),
            episode_infos=list(meta.get("episode_infos") or []),
            rewards_softlock=np.asarray(softlock, dtype=np.float32),
            combat_targets=combat_targets,
            world_event_targets=world_event_targets,
            world_event_masks=world_event_masks,
            mod_drop_masks=mod_drop_masks,
            curriculum_id=str(meta.get("curriculum_id") or ""),
            obs_schema_version=int(meta.get("obs_schema_version") or 0),
        )
