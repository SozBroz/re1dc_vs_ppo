"""Rollout batch exchanged between workers and the learner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


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

    def num_timesteps(self) -> int:
        return int(self.n_envs * self.n_steps)

    def softlock_rewards(self) -> np.ndarray:
        if self.rewards_softlock is None:
            return np.zeros_like(self.rewards, dtype=np.float32)
        return np.asarray(self.rewards_softlock, dtype=np.float32)
