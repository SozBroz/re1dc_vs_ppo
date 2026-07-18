"""Tests for VecEnv collect_rollout continue-across-horizon mode."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3.common.vec_env import DummyVecEnv

from re1_rl.distributed.rollout_collect import collect_rollout


class _StubPolicy:
    policy_version = 1

    def __init__(self, n_actions: int = 4) -> None:
        self._n_actions = n_actions

    def predict_masked_batch(
        self, obs: dict[str, np.ndarray], masks: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        n = masks.shape[0]
        act = np.zeros(n, dtype=np.int64)
        val = np.zeros(n, dtype=np.float32)
        lp = np.zeros(n, dtype=np.float32)
        return act, val, lp

    def predict_values(self, obs: dict[str, np.ndarray]) -> np.ndarray:
        return np.zeros(next(iter(obs.values())).shape[0], dtype=np.float32)


class _CountingEnv(gym.Env):
    """Dict obs env that increments a counter on each step (no auto-reset)."""

    metadata = {"render_modes": []}

    def __init__(self) -> None:
        super().__init__()
        self.observation_space = spaces.Dict(
            {"x": spaces.Box(0, 1e9, shape=(1,), dtype=np.float32)}
        )
        self.action_space = spaces.Discrete(4)
        self._t = 0

    def reset(self, *, seed=None, options=None):
        self._t = 0
        return {"x": np.array([float(self._t)], dtype=np.float32)}, {}

    def step(self, action):
        self._t += 1
        obs = {"x": np.array([float(self._t)], dtype=np.float32)}
        info = {"reward_breakdown": {"softlock": 0.0}}
        return obs, 0.0, False, False, info

    def action_masks(self):
        return np.ones(4, dtype=bool)


def test_collect_rollout_continue_does_not_reset_between_horizons() -> None:
    vec = DummyVecEnv([_CountingEnv, _CountingEnv])
    policy = _StubPolicy()

    r1, obs1 = collect_rollout(
        vec, policy, n_steps=3, worker_id="w", obs=None  # type: ignore[arg-type]
    )
    assert r1.n_steps == 3
    assert r1.n_envs == 2
    assert r1.rewards_softlock is None
    # After 3 steps, each env counter is 3 (stored in next obs).
    assert float(obs1["x"][0, 0]) == 3.0
    assert float(obs1["x"][1, 0]) == 3.0

    r2, obs2 = collect_rollout(
        vec, policy, n_steps=2, worker_id="w", obs=obs1  # type: ignore[arg-type]
    )
    assert r2.n_steps == 2
    # Continued — counters advance to 5, not reset to 2.
    assert float(obs2["x"][0, 0]) == 5.0
    assert float(obs2["x"][1, 0]) == 5.0
    # First stored obs of second horizon should be the prior next-obs (t=3).
    assert float(r2.obs["x"][0, 0, 0]) == 3.0
