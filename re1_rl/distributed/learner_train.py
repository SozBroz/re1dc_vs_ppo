"""Learner-side rollout ingestion and PPO updates."""

from __future__ import annotations

import queue
import time
from typing import Any

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.buffers import DictRolloutBuffer
from stable_baselines3.common.callbacks import BaseCallback

from re1_rl.distributed.log_util import log
from re1_rl.distributed.obs_preprocess import prepare_obs_for_policy
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.distributed.weights import export_policy_state_dict
from re1_rl.training_metrics_log import ensure_training_logger


def _episode_starts_from_dones(dones: np.ndarray) -> np.ndarray:
    starts = np.zeros_like(dones, dtype=np.bool_)
    starts[0] = True
    if dones.shape[0] > 1:
        starts[1:] = dones[:-1]
    return starts


def merge_rollouts(rollouts: list[WorkerRollout]) -> dict[str, Any]:
    if not rollouts:
        raise ValueError("empty rollout batch")
    n_steps = rollouts[0].n_steps
    for r in rollouts:
        if r.n_steps != n_steps:
            raise ValueError("all rollouts in a batch must share n_steps")

    total_envs = sum(r.n_envs for r in rollouts)
    obs: dict[str, np.ndarray] = {}
    for key in rollouts[0].obs:
        obs[key] = np.concatenate([r.obs[key] for r in rollouts], axis=1)

    actions = np.concatenate([r.actions for r in rollouts], axis=1)
    rewards = np.concatenate([r.rewards for r in rollouts], axis=1)
    dones = np.concatenate([r.dones for r in rollouts], axis=1)
    values = np.concatenate([r.values for r in rollouts], axis=1)
    log_probs = np.concatenate([r.log_probs for r in rollouts], axis=1)
    last_values = np.concatenate([r.last_values for r in rollouts], axis=0)
    episode_starts = _episode_starts_from_dones(dones)

    return {
        "n_steps": n_steps,
        "n_envs": total_envs,
        "obs": obs,
        "actions": actions,
        "rewards": rewards,
        "dones": dones,
        "values": values,
        "log_probs": log_probs,
        "last_values": last_values,
        "episode_starts": episode_starts,
    }


def _obs_step_for_buffer(
    obs_step: dict[str, np.ndarray],
    observation_space,
) -> dict[str, np.ndarray]:
    return prepare_obs_for_policy(obs_step, observation_space)


def fill_rollout_buffer(model: PPO, merged: dict[str, Any]) -> DictRolloutBuffer:
    buffer = DictRolloutBuffer(
        merged["n_steps"],
        model.observation_space,
        model.action_space,
        device=model.device,
        gamma=model.gamma,
        gae_lambda=model.gae_lambda,
        n_envs=merged["n_envs"],
    )
    buffer.reset()
    n_steps = merged["n_steps"]
    for step in range(n_steps):
        obs_step = _obs_step_for_buffer(
            {k: merged["obs"][k][step] for k in merged["obs"]},
            model.observation_space,
        )
        buffer.add(
            obs_step,
            merged["actions"][step],
            merged["rewards"][step],
            merged["episode_starts"][step],
            torch.as_tensor(merged["values"][step], device=model.device),
            torch.as_tensor(merged["log_probs"][step], device=model.device),
        )
    last_values = torch.as_tensor(merged["last_values"], device=model.device)
    buffer.compute_returns_and_advantage(last_values, merged["dones"][n_steps - 1])
    return buffer


def train_on_rollouts(model: PPO, rollouts: list[WorkerRollout]) -> int:
    merged = merge_rollouts(rollouts)
    model.rollout_buffer = fill_rollout_buffer(model, merged)
    ensure_training_logger(model)
    model.train()
    timesteps = merged["n_steps"] * merged["n_envs"]
    model.num_timesteps += int(timesteps)
    if getattr(model, "logger", None) is not None:
        model.logger.dump(step=int(model.num_timesteps))
    return int(timesteps)


def run_learner_loop(
    model: PPO,
    weight_store: WeightStore,
    rollout_queue: queue.Queue[WorkerRollout],
    *,
    machine_name: str,
    batch_threshold: int,
    learner_state: Any,
    callbacks: list[BaseCallback] | None = None,
    stop_event: Any | None = None,
    queue_timeout_s: float = 5.0,
) -> None:
    pending: list[WorkerRollout] = []
    pending_steps = 0
    callbacks = callbacks or []

    for cb in callbacks:
        cb.init_callback(model)

    log(machine_name, "learner loop started")

    while stop_event is None or not stop_event.is_set():
        try:
            rollout = rollout_queue.get(timeout=queue_timeout_s)
            pending.append(rollout)
            pending_steps += rollout.num_timesteps()
            log(
                machine_name,
                f"queued rollout from {rollout.worker_id} "
                f"v{rollout.policy_version} (+{rollout.num_timesteps()} steps, "
                f"pending={pending_steps})",
            )
        except queue.Empty:
            pass

        if pending_steps < batch_threshold:
            continue

        try:
            trained_steps = train_on_rollouts(model, pending)
            version = weight_store.publish(export_policy_state_dict(model))
            learner_state.set_current_version(version)
            log(
                machine_name,
                f"trained on {len(pending)} rollouts ({trained_steps} steps) "
                f"-> policy_version={version} total_steps={model.num_timesteps}",
            )
            for cb in callbacks:
                cb.on_rollout_end()
                cb.on_step()
        except Exception as exc:
            log(machine_name, f"train failed: {exc}")
            raise
        finally:
            pending.clear()
            pending_steps = 0

    log(machine_name, "learner loop stopped")
