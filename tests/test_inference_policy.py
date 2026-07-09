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


def _hwc_batch(policy_obs_space):
    return {
        "frame": np.zeros((1, 84, 84, 4), dtype=np.uint8),
        "proprio": np.zeros(
            (1, policy_obs_space.spaces["proprio"].shape[0]), dtype=np.float32
        ),
        "goal": np.zeros(
            (1, policy_obs_space.spaces["goal"].shape[0]), dtype=np.float32
        ),
        "spatial": np.zeros(
            (1, policy_obs_space.spaces["spatial"].shape[0]), dtype=np.float32
        ),
        "visited": np.zeros(
            (1, *policy_obs_space.spaces["visited"].shape), dtype=np.float32
        ),
        "rooms_visited": np.zeros(
            (1, policy_obs_space.spaces["rooms_visited"].shape[0]), dtype=np.float32
        ),
        "box": np.zeros(
            (1, policy_obs_space.spaces["box"].shape[0]), dtype=np.float32
        ),
    }


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
