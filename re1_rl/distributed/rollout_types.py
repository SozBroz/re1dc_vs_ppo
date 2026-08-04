"""Rollout batch exchanged between workers and the learner."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

def normalize_curriculum_id(curriculum: str | Path | None) -> str:
    """Stable curriculum identity for ingest matching (posix, curriculum/…)."""
    if curriculum is None:
        return ""
    s = str(curriculum).replace("\\", "/").strip()
    if not s:
        return ""
    marker = "curriculum/"
    idx = s.rfind(marker)
    if idx >= 0:
        return s[idx:]
    return Path(s).name


@dataclass
class WorkerRollout:
    worker_id: str
    policy_version: int
    n_envs: int
    n_steps: int
    obs: dict[str, np.ndarray]
    actions: np.ndarray
    rewards: np.ndarray
    dones: np.ndarray
    values: np.ndarray
    log_probs: np.ndarray
    last_values: np.ndarray
    # (n_steps, n_envs, n_actions) bool — required for MaskablePPO train parity
    action_masks: np.ndarray
    episode_infos: list[dict[str, Any]] = field(default_factory=list)
    # Legacy optional field; unused (softlock is in scalar rewards).
    rewards_softlock: np.ndarray | None = None
    # Aligned aux targets: (n_steps, n_envs, dim); optional for back-compat decode.
    combat_targets: np.ndarray | None = None
    world_event_targets: np.ndarray | None = None
    world_event_masks: np.ndarray | None = None
    # Policy-consistent ModDrop presence (n_steps, n_envs, MOD_DROP_DIM); optional.
    mod_drop_masks: np.ndarray | None = None
    # Fleet identity — empty / 0 means legacy (pre-identity) payload.
    curriculum_id: str = ""
    obs_schema_version: int = 0

    def num_timesteps(self) -> int:
        return int(self.n_envs * self.n_steps)

    def softlock_rewards(self) -> np.ndarray:
        if self.rewards_softlock is None:
            return np.zeros_like(self.rewards, dtype=np.float32)
        return np.asarray(self.rewards_softlock, dtype=np.float32)
