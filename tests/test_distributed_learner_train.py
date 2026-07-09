"""Learner train step on merged worker rollouts."""

from __future__ import annotations

import sys
from pathlib import Path

import gymnasium as gym
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_train import train_on_rollouts
from re1_rl.env import ACTION_NAMES
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.spaces import make_re1_spaces
from re1_rl.policy_config import POLICY_KWARGS

N_ACTIONS = len(ACTION_NAMES)


def _tiny_model() -> PPO:
    obs_space, act_space = make_re1_spaces()

    class _StubEnv(gym.Env):
        def __init__(self) -> None:
            super().__init__()
            self.observation_space = obs_space
            self.action_space = act_space

        def reset(self, *, seed=None, options=None):
            return {k: s.sample() for k, s in self.observation_space.items()}, {}

        def step(self, action):
            obs, _ = self.reset()
            return obs, 0.0, False, False, {}

    env = DummyVecEnv([lambda: _StubEnv()])
    return PPO(
        "MultiInputPolicy",
        env,
        policy_kwargs=POLICY_KWARGS,
        n_steps=8,
        batch_size=8,
        n_epochs=1,
        device="cpu",
        verbose=0,
    )


def _fake_rollout(n_steps: int = 8, n_envs: int = 2, version: int = 1) -> WorkerRollout:
    obs_space, _ = make_re1_spaces()
    obs = {
        key: np.zeros((n_steps, n_envs, *space.shape), dtype=space.dtype)
        for key, space in obs_space.spaces.items()
    }
    obs["frame"] = np.random.randint(
        0, 255, (n_steps, n_envs, 84, 84, 4), dtype=np.uint8
    )
    return WorkerRollout(
        worker_id="w",
        policy_version=version,
        n_envs=n_envs,
        n_steps=n_steps,
        obs=obs,
        actions=np.random.randint(0, N_ACTIONS, (n_steps, n_envs), dtype=np.int64),
        rewards=np.random.randn(n_steps, n_envs).astype(np.float32),
        dones=np.zeros((n_steps, n_envs), dtype=np.bool_),
        values=np.random.randn(n_steps, n_envs).astype(np.float32),
        log_probs=np.random.randn(n_steps, n_envs).astype(np.float32) * 0.01,
        last_values=np.random.randn(n_envs).astype(np.float32),
    )


def test_train_on_rollouts_advances_timesteps() -> None:
    model = _tiny_model()
    before = model.num_timesteps
    steps = train_on_rollouts(model, [_fake_rollout()])
    assert steps == 16
    assert model.num_timesteps == before + 16
