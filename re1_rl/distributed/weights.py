"""Policy weight serialization and inference-only policy construction."""

from __future__ import annotations

import io
from typing import Any

import gymnasium as gym
import torch
from stable_baselines3 import PPO

from re1_rl.policy_config import POLICY_KWARGS


def policy_bytes_from_state_dict(state_dict: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    torch.save(state_dict, buf)
    return buf.getvalue()


def state_dict_from_policy_bytes(data: bytes) -> dict[str, Any]:
    buf = io.BytesIO(data)
    return torch.load(buf, map_location="cpu", weights_only=True)


class _SpaceHolderEnv(gym.Env):
    def __init__(self, observation_space: gym.Space, action_space: gym.Space) -> None:
        super().__init__()
        self.observation_space = observation_space
        self.action_space = action_space


def build_inference_policy(
    observation_space: gym.Space,
    action_space: gym.Space,
    device: str | torch.device,
) -> PPO:
    """Construct a PPO shell used only for ``policy`` inference (no optimizer)."""
    from re1_rl.async_fleet import PPO_HYPERPARAMS

    return PPO(
        "MultiInputPolicy",
        _SpaceHolderEnv(observation_space, action_space),
        policy_kwargs=POLICY_KWARGS,
        verbose=0,
        device=device,
        **PPO_HYPERPARAMS,
    )


def load_policy_weights(model: PPO, state_dict: dict[str, Any]) -> None:
    model.policy.load_state_dict(state_dict, strict=True)
    model.policy.set_training_mode(False)


def export_policy_state_dict(model: PPO) -> dict[str, Any]:
    return {k: v.detach().cpu().clone() for k, v in model.policy.state_dict().items()}
