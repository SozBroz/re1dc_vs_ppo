"""InferencePolicy accepts env-native HWC frame observations."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.inference_policy import InferencePolicy
from re1_rl.distributed.obs_preprocess import prepare_obs_for_policy
from re1_rl.distributed.spaces import make_re1_spaces


def _chw_obs_space():
    obs_space, act_space = make_re1_spaces()
    chw_space = obs_space.spaces["frame"].__class__(
        low=0,
        high=255,
        shape=(4, 84, 84),
        dtype=np.uint8,
    )
    policy_obs_space = obs_space.__class__(
        {**obs_space.spaces, "frame": chw_space}
    )
    return policy_obs_space, act_space


def _hwc_batch(policy_obs_space, n_envs: int = 1):
    batch: dict[str, np.ndarray] = {}
    for key, space in policy_obs_space.spaces.items():
        if key == "frame":
            # Env-native HWC; prepare_obs_for_policy transposes to CHW.
            batch[key] = np.zeros((n_envs, 84, 84, 4), dtype=np.uint8)
        else:
            batch[key] = np.zeros((n_envs, *space.shape), dtype=space.dtype)
    return batch


def test_inference_policy_accepts_hwc_frame_batch() -> None:
    policy_obs_space, act_space = _chw_obs_space()
    policy = InferencePolicy(policy_obs_space, act_space, device="cpu")
    actions, values, log_probs = policy.predict_batch(_hwc_batch(policy_obs_space))
    assert actions.shape == (1,)
    assert values.shape == (1,)
    assert log_probs.shape == (1,)


def test_prepare_obs_transposes_batched_frame() -> None:
    policy_obs_space, _ = _chw_obs_space()
    hwc = np.zeros((2, 84, 84, 4), dtype=np.uint8)
    out = prepare_obs_for_policy({"frame": hwc}, policy_obs_space)
    assert out["frame"].shape == (2, 4, 84, 84)


def test_predict_masked_batch_never_samples_illegal() -> None:
    """Illegal actions (mask False) must not be sampled; logits go to -inf."""
    policy_obs_space, act_space = _chw_obs_space()
    n_actions = int(act_space.n)
    policy = InferencePolicy(policy_obs_space, act_space, device="cpu")
    n_envs = 2
    obs = _hwc_batch(policy_obs_space, n_envs=n_envs)
    # Env 0: only action 3 legal. Env 1: only action 7 legal.
    masks = np.zeros((n_envs, n_actions), dtype=bool)
    masks[0, 3] = True
    masks[1, 7] = True

    for _ in range(32):
        actions, values, log_probs = policy.predict_masked_batch(obs, masks)
        assert actions.shape == (n_envs,)
        assert values.shape == (n_envs,)
        assert log_probs.shape == (n_envs,)
        assert int(actions[0]) == 3
        assert int(actions[1]) == 7

    # Single-env path still returns scalars and respects the mask.
    one_mask = masks[0]
    for _ in range(16):
        act, _val, _lp = policy.predict_masked(
            {k: v[:1] for k, v in obs.items()}, one_mask
        )
        assert act == 3


def test_stack_action_masks_from_vec_env() -> None:
    """Subproc/DummyVecEnv expose masks via env_method('action_masks')."""
    import gymnasium as gym
    from gymnasium import spaces
    from stable_baselines3.common.vec_env import DummyVecEnv

    from re1_rl.distributed.rollout_collect import _stack_action_masks

    n_actions = 5

    class _MaskedStub(gym.Env):
        def __init__(self, legal: int) -> None:
            super().__init__()
            self.observation_space = spaces.Box(0, 1, shape=(1,), dtype=np.float32)
            self.action_space = spaces.Discrete(n_actions)
            self._legal = legal

        def reset(self, *, seed=None, options=None):
            return np.zeros(1, dtype=np.float32), {}

        def step(self, action):
            return np.zeros(1, dtype=np.float32), 0.0, False, False, {}

        def action_masks(self):
            m = np.zeros(n_actions, dtype=bool)
            m[self._legal] = True
            return m

    vec = DummyVecEnv([lambda: _MaskedStub(1), lambda: _MaskedStub(4)])
    stacked = _stack_action_masks(vec)
    assert stacked.shape == (2, n_actions)
    assert stacked.dtype == bool
    assert stacked[0].tolist() == [False, True, False, False, False]
    assert stacked[1].tolist() == [False, False, False, False, True]
