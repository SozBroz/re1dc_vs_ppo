"""Learner-side rollout ingestion and PPO updates."""

from __future__ import annotations

import gc
import queue
import time
from typing import Any

import numpy as np
import torch
from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.buffers import MaskableDictRolloutBuffer
from stable_baselines3.common.callbacks import BaseCallback

from re1_rl.distributed.log_util import log
from re1_rl.distributed.obs_preprocess import prepare_obs_for_policy
from re1_rl.distributed.rollout_types import WorkerRollout
from re1_rl.distributed.weight_store import WeightStore
from re1_rl.distributed.weights import export_policy_state_dict
from re1_rl.training_metrics_log import ensure_training_logger


def pull_rollout_queue(
    rollout_queue: queue.Queue[WorkerRollout],
    pending: list[WorkerRollout],
    *,
    machine_name: str = "",
) -> int:
    """Move queued rollouts into ``pending`` (never discard)."""
    moved = 0
    while True:
        try:
            pending.append(rollout_queue.get_nowait())
            moved += 1
        except queue.Empty:
            break
    if moved and machine_name:
        steps = sum(r.num_timesteps() for r in pending[-moved:])
        log(
            machine_name,
            f"carried forward {moved} queued rollouts ({steps} env-steps) to next epoch",
        )
    return moved


def _release_rollout_arrays(rollouts: list[WorkerRollout]) -> None:
    for r in rollouts:
        r.obs.clear()
        r.actions = np.empty(0)
        r.rewards = np.empty(0)
        r.dones = np.empty(0)
        r.values = np.empty(0)
        r.log_probs = np.empty(0)
        r.last_values = np.empty(0)
        r.action_masks = np.empty(0)


def _episode_starts_from_dones(dones: np.ndarray) -> np.ndarray:
    starts = np.zeros_like(dones, dtype=np.bool_)
    starts[0] = True
    if dones.shape[0] > 1:
        starts[1:] = dones[:-1]
    return starts


def group_rollouts_by_policy_version(
    rollouts: list[WorkerRollout],
) -> dict[int, list[WorkerRollout]]:
    groups: dict[int, list[WorkerRollout]] = {}
    for r in rollouts:
        groups.setdefault(int(r.policy_version), []).append(r)
    return groups


def merge_rollouts(rollouts: list[WorkerRollout]) -> dict[str, Any]:
    if not rollouts:
        raise ValueError("empty rollout batch")
    n_steps = rollouts[0].n_steps
    versions = {int(r.policy_version) for r in rollouts}
    if len(versions) != 1:
        raise ValueError(
            f"merge_rollouts requires a single policy_version, got {sorted(versions)}"
        )
    for r in rollouts:
        if r.n_steps != n_steps:
            raise ValueError("all rollouts in a batch must share n_steps")
        if r.action_masks is None or r.action_masks.size == 0:
            raise ValueError("rollout missing action_masks (fail closed)")

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
    action_masks = np.concatenate([r.action_masks for r in rollouts], axis=1)
    episode_starts = _episode_starts_from_dones(dones)

    return {
        "n_steps": n_steps,
        "n_envs": total_envs,
        "policy_version": int(rollouts[0].policy_version),
        "obs": obs,
        "actions": actions,
        "rewards": rewards,
        "dones": dones,
        "values": values,
        "log_probs": log_probs,
        "last_values": last_values,
        "action_masks": action_masks,
        "episode_starts": episode_starts,
    }


def _obs_step_for_buffer(
    obs_step: dict[str, np.ndarray],
    observation_space,
) -> dict[str, np.ndarray]:
    return prepare_obs_for_policy(obs_step, observation_space)


def fill_rollout_buffer(model: MaskablePPO, merged: dict[str, Any]) -> MaskableDictRolloutBuffer:
    buffer = MaskableDictRolloutBuffer(
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
            action_masks=merged["action_masks"][step],
        )
    last_values = torch.as_tensor(merged["last_values"], device=model.device)
    buffer.compute_returns_and_advantage(last_values, merged["dones"][n_steps - 1])
    return buffer


def _train_one_version(
    model: MaskablePPO,
    rollouts: list[WorkerRollout],
    *,
    machine_name: str = "",
) -> int:
    merged = merge_rollouts(rollouts)
    n_envs = int(merged["n_envs"])
    n_steps = int(merged["n_steps"])
    version = int(merged["policy_version"])
    if machine_name:
        log(
            machine_name,
            f"merge_rollouts: policy_version={version} n_envs={n_envs} "
            f"n_steps={n_steps} timesteps={n_envs * n_steps}",
        )
    model.rollout_buffer = fill_rollout_buffer(model, merged)
    ensure_training_logger(model)
    model.train()
    timesteps = n_steps * n_envs
    model.num_timesteps += int(timesteps)
    if getattr(model, "logger", None) is not None:
        model.logger.dump(step=int(model.num_timesteps))
    merged.clear()
    return int(timesteps)


def train_on_rollouts(
    model: MaskablePPO,
    rollouts: list[WorkerRollout],
    *,
    machine_name: str = "",
) -> int:
    """Train PPO on rollouts, one homogeneous ``policy_version`` at a time (oldest first)."""
    if not rollouts:
        return 0
    groups = group_rollouts_by_policy_version(rollouts)
    total = 0
    try:
        for version in sorted(groups):
            total += _train_one_version(
                model,
                groups[version],
                machine_name=machine_name,
            )
        return total
    finally:
        _release_rollout_arrays(rollouts)
        gc.collect()


def run_learner_loop(
    model: MaskablePPO,
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
            trained_steps = train_on_rollouts(model, pending, machine_name=machine_name)
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
