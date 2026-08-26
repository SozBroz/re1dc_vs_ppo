"""Observation layout helpers for distributed training."""

from __future__ import annotations

import numpy as np
from gymnasium import spaces
from stable_baselines3.common.preprocessing import is_image_space


def _frame_to_policy_layout(arr: np.ndarray, expected: tuple[int, ...]) -> np.ndarray:
    """Single HWC->CHW transpose at pack time; skip if already CHW."""
    if len(expected) != 3:
        return arr
    if arr.ndim == 3:
        got = arr.shape
        if got == expected:
            return arr
        h, w, c = got
        if (c, h, w) == expected:
            return np.transpose(arr, (2, 0, 1))
        return arr
    if arr.ndim == 4:
        got = arr.shape[1:]
        if got == expected:
            return arr
        if len(got) == 3:
            h, w, c = got
            if (c, h, w) == expected:
                return np.transpose(arr, (0, 3, 1, 2))
    return arr


def prepare_obs_for_policy(
    obs: dict[str, np.ndarray],
    observation_space: spaces.Dict,
) -> dict[str, np.ndarray]:
    """Match SB3 VecTransposeImage: env-native HWC -> policy CHW for images."""
    out: dict[str, np.ndarray] = {}
    for key, val in obs.items():
        space = observation_space.spaces[key]
        arr = np.asarray(val)
        if is_image_space(space):
            arr = _frame_to_policy_layout(arr, tuple(space.shape))
        out[key] = arr
    return out
