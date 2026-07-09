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
    episode_infos: list[dict[str, Any]] = field(default_factory=list)

    def num_timesteps(self) -> int:
        return int(self.n_envs * self.n_steps)
