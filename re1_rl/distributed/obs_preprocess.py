"""Observation layout helpers for distributed training."""

from __future__ import annotations

import numpy as np
from gymnasium import spaces
from stable_baselines3.common.preprocessing import is_image_space


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
            expected = space.shape
            if arr.ndim == 3:
                got = arr.shape
            elif arr.ndim == 4:
                got = arr.shape[1:]
            else:
                got = arr.shape
            if len(got) == 3 and len(expected) == 3 and got != expected:
                h, w, c = got
                if (c, h, w) == expected:
                    if arr.ndim == 3:
                        arr = np.transpose(arr, (2, 0, 1))
                    else:
                        arr = np.transpose(arr, (0, 3, 1, 2))
        out[key] = arr
    return out
