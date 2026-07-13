"""Learner train step on merged worker rollouts."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import torch
from sb3_contrib import MaskablePPO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_train import (
    fill_rollout_buffer,
    group_rollouts_by_policy_version,
    merge_rollouts,
    train_on_rollouts,
)
from re1_rl.env import ACTION_NAMES
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.spaces import make_re1_policy_spaces, make_re1_spaces
from re1_rl.distributed.weights import _SpaceHolderEnv
from re1_rl.policy_config import POLICY_KWARGS

N_ACTIONS = len(ACTION_NAMES)


def _tiny_model() -> MaskablePPO:
    """Learner-shaped MaskablePPO (CHW frame), matching load_async_learner / workers."""
    obs_space, act_space = make_re1_policy_spaces()
    return MaskablePPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(obs_space, act_space),
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
        0, 255, (n_steps, n_envs, *obs_space["frame"].shape), dtype=np.uint8
    )
    masks = np.ones((n_steps, n_envs, N_ACTIONS), dtype=np.bool_)
    masks[..., N_ACTIONS // 2 :] = False
    return WorkerRollout(
        worker_id="w",
        policy_version=version,
        n_envs=n_envs,
        n_steps=n_steps,
        obs=obs,
        actions=np.random.randint(0, N_ACTIONS // 2, (n_steps, n_envs), dtype=np.int64),
        rewards=np.random.randn(n_steps, n_envs).astype(np.float32),
        dones=np.zeros((n_steps, n_envs), dtype=np.bool_),
        values=np.random.randn(n_steps, n_envs).astype(np.float32),
        log_probs=np.random.randn(n_steps, n_envs).astype(np.float32) * 0.01,
        last_values=np.random.randn(n_envs).astype(np.float32),
        action_masks=masks,
    )


def test_train_on_rollouts_advances_timesteps() -> None:
    model = _tiny_model()
    before = model.num_timesteps
    steps = train_on_rollouts(model, [_fake_rollout()])
    assert steps == 16
    assert model.num_timesteps == before + 16


def test_merge_rollouts_rejects_mixed_policy_versions() -> None:
    a = _fake_rollout(version=1)
    b = _fake_rollout(version=2)
    with pytest.raises(ValueError, match="single policy_version"):
        merge_rollouts([a, b])


def test_train_on_rollouts_partitions_mixed_versions() -> None:
    model = _tiny_model()
    before = model.num_timesteps
    steps = train_on_rollouts(
        model,
        [_fake_rollout(version=1), _fake_rollout(version=3)],
    )
    assert steps == 32
    assert model.num_timesteps == before + 32
    groups = group_rollouts_by_policy_version(
        [_fake_rollout(version=1), _fake_rollout(version=3)]
    )
    assert sorted(groups) == [1, 3]


def test_fill_buffer_stores_action_masks() -> None:
    model = _tiny_model()
    merged = merge_rollouts([_fake_rollout()])
    buf = fill_rollout_buffer(model, merged)
    assert buf.action_masks is not None
    assert buf.action_masks.shape == (
        merged["n_steps"],
        merged["n_envs"],
        N_ACTIONS,
    )


def test_train_survives_trailing_size_one_minibatch() -> None:
    """Regression: 2049 samples @ batch 2048 poisons MaskablePPO advantage std."""
    model = _tiny_model()
    model.batch_size = 4
    model.normalize_advantage = True
    # 9 env-steps -> minibatches 4 + 4 + 1 (the trailing 1 used to NaN std).
    steps = train_on_rollouts(model, [_fake_rollout(n_steps=3, n_envs=3)])
    assert steps == 9
    for param in model.policy.parameters():
        assert torch.isfinite(param).all()
