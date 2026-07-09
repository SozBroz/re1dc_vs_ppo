"""Thread-safe inference-only policy mirror for rollout workers."""

from __future__ import annotations

import threading
from typing import Any

import gymnasium as gym
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.utils import obs_as_tensor

from re1_rl.distributed.obs_preprocess import prepare_obs_for_policy
from re1_rl.distributed.weights import (
    build_inference_policy,
    load_policy_weights,
    state_dict_from_policy_bytes,
)


class InferencePolicy:
    """Local policy mirror; workers must never load checkpoints from disk."""

    def __init__(
        self,
        observation_space: gym.Space,
        action_space: gym.Space,
        device: str | torch.device,
    ) -> None:
        self._device = torch.device(device)
        self._lock = threading.RLock()
        self._policy_version = 0
        self._model = build_inference_policy(observation_space, action_space, self._device)
        self._model.policy.to(self._device)
        self._model.policy.set_training_mode(False)
        # SB3 stores spaces on the model after env attach; set explicitly.
        self._model.observation_space = observation_space
        self._model.action_space = action_space

    @property
    def policy_version(self) -> int:
        with self._lock:
            return self._policy_version

    def load_from_state_dict(self, state_dict: dict[str, Any], policy_version: int) -> None:
        with self._lock:
            load_policy_weights(self._model, state_dict)
            self._policy_version = policy_version

    def load_from_bytes(self, policy_bytes: bytes, policy_version: int) -> None:
        state_dict = state_dict_from_policy_bytes(policy_bytes)
        self.load_from_state_dict(state_dict, policy_version)

    def predict_batch(self, obs: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with self._lock:
            obs = prepare_obs_for_policy(obs, self._model.observation_space)
            obs_tensor = obs_as_tensor(obs, self._device)
            with torch.no_grad():
                actions, values, log_probs = self._model.policy(obs_tensor)
            return (
                actions.cpu().numpy(),
                values.flatten().cpu().numpy(),
                log_probs.cpu().numpy(),
            )

    def predict_values(self, obs: dict[str, np.ndarray]) -> np.ndarray:
        with self._lock:
            obs = prepare_obs_for_policy(obs, self._model.observation_space)
            obs_tensor = obs_as_tensor(obs, self._device)
            with torch.no_grad():
                values = self._model.policy.predict_values(obs_tensor)
            return values.flatten().cpu().numpy()

    def predict_masked(
        self,
        obs: dict[str, np.ndarray],
        action_masks: np.ndarray,
    ) -> tuple[int, float, float]:
        """Sample one action with invalid logits masked to -inf."""
        with self._lock:
            obs = prepare_obs_for_policy(obs, self._model.observation_space)
            obs_tensor = obs_as_tensor(obs, self._device)
            mask = torch.as_tensor(action_masks, device=self._device, dtype=torch.bool)
            if mask.ndim == 1:
                mask = mask.unsqueeze(0)
            with torch.no_grad():
                dist = self._model.policy.get_distribution(obs_tensor)
                logits = dist.distribution.logits.clone()
                logits[~mask] = torch.finfo(logits.dtype).min
                cat = torch.distributions.Categorical(logits=logits)
                actions = cat.sample()
                log_prob = cat.log_prob(actions)
                values = self._model.policy.predict_values(obs_tensor)
            return (
                int(actions[0].item()),
                float(values[0].item()),
                float(log_prob[0].item()),
            )
