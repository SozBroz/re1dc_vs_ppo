"""Collect (InferencePolicy) vs train (MaskablePPO) masked logprob parity."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.async_fleet import load_async_learner
from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.obs_preprocess import prepare_obs_for_policy
from re1_rl.distributed.spaces import make_re1_policy_spaces
from re1_rl.env import ACTION_NAMES
from stable_baselines3.common.utils import obs_as_tensor

N_ACTIONS = len(ACTION_NAMES)


def test_masked_collect_matches_maskable_evaluate_actions() -> None:
    obs_space, act_space = make_re1_policy_spaces()
    policy = InferencePolicy(obs_space, act_space, "cpu")
    model = load_async_learner(device="cpu", resume=None, tb_log=None)
    policy.load_from_state_dict(
        {k: v.detach().cpu().clone() for k, v in model.policy.state_dict().items()},
        policy_version=1,
    )

    n_envs = 4
    rng = np.random.default_rng(0)
    obs = {
        k: np.stack([space.sample() for _ in range(n_envs)], axis=0)
        for k, space in obs_space.spaces.items()
    }
    masks = np.zeros((n_envs, N_ACTIONS), dtype=bool)
    for i in range(n_envs):
        legal = rng.choice(N_ACTIONS, size=max(3, N_ACTIONS // 6), replace=False)
        masks[i, legal] = True
        masks[i, 0] = True

    actions, _values, log_collect = policy.predict_masked_batch(obs, masks)
    obs_t = obs_as_tensor(prepare_obs_for_policy(obs, model.observation_space), "cpu")
    act_t = torch.as_tensor(actions, dtype=torch.int64)
    _v, log_train, _e = model.policy.evaluate_actions(obs_t, act_t, action_masks=masks)
    delta = np.abs(log_collect - log_train.detach().cpu().numpy())
    assert float(delta.max()) < 1e-4, f"max |Δ|={delta.max()}"
