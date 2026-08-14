"""Per-leg policy dump: chosen action, legal mask, masked odds.

Sibling to ``leg_replay.json`` as ``leg_policy.npz``. Obs / audio / frames
are not stored — replay the tape later and rebuild those.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.go_explore_merge import CELL_POLICY_NAME

SCHEMA_VERSION = 1
N_ACTIONS = 45


def new_footage_trace_buffer() -> "FootageTraceBuffer":
    return FootageTraceBuffer()


class FootageTraceBuffer:
    """In-memory legal-action + odds tape. Written only on cell capture."""

    __slots__ = ("action", "action_mask", "masked_probs", "policy_version")

    def __init__(self) -> None:
        self.action: list[int] = []
        self.action_mask: list[np.ndarray] = []
        self.masked_probs: list[np.ndarray] = []
        self.policy_version = 0

    def __len__(self) -> int:
        return len(self.action)

    def append(
        self,
        *,
        action: int,
        action_mask: Any,
        masked_probs: Any | None,
        policy_version: int = 0,
        n_actions: int = N_ACTIONS,
    ) -> None:
        n = max(1, int(n_actions))
        mask = np.asarray(action_mask, dtype=np.bool_).reshape(-1)
        if mask.size < n:
            pad = np.zeros(n, dtype=np.bool_)
            pad[: mask.size] = mask
            mask = pad
        self.action.append(int(action))
        self.action_mask.append(mask[:n].copy())
        self.masked_probs.append(_vec(masked_probs, n))
        self.policy_version = int(policy_version)

    def write(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = len(self)
        np.savez_compressed(
            dest,
            schema_version=np.int16(SCHEMA_VERSION),
            policy_version=np.int32(self.policy_version),
            action=np.asarray(self.action, dtype=np.int16),
            action_mask=np.stack(self.action_mask, axis=0)
            if n
            else np.zeros((0, N_ACTIONS), dtype=np.bool_),
            masked_probs=np.stack(self.masked_probs, axis=0)
            if n
            else np.zeros((0, N_ACTIONS), dtype=np.float32),
        )
        return dest


def maybe_write_footage_trace(
    env: Any,
    staging: Path,
    *,
    completed_index: int,
) -> Path | None:
    """Write ``leg_policy.npz`` into staging on a single-leg capture."""
    from re1_rl.leg_replay import should_write_leg_replay

    if not should_write_leg_replay(env, completed_index):
        return None
    buf = getattr(env, "_footage_trace", None)
    if not isinstance(buf, FootageTraceBuffer) or len(buf) == 0:
        return None
    return buf.write(Path(staging) / CELL_POLICY_NAME)


def estimate_policy_bytes(n_steps: int, n_actions: int = N_ACTIONS) -> int:
    """Uncompressed bytes for action + mask + float32 odds."""
    return int(n_steps) * (2 + n_actions + n_actions * 4)


def _vec(value: Any, n: int) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)
    if value is None:
        return out
    arr = np.asarray(value, dtype=np.float32).reshape(-1)
    out[: min(n, arr.size)] = arr[:n]
    return out
