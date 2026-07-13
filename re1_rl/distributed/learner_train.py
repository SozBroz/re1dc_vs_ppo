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

        r.rewards_softlock = None





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


def group_rollouts_for_train(
    rollouts: list[WorkerRollout],
) -> dict[tuple[int, int], list[WorkerRollout]]:
    """Group by (policy_version, n_steps) for merge + buffer sizing."""
    groups: dict[tuple[int, int], list[WorkerRollout]] = {}
    for r in rollouts:
        key = (int(r.policy_version), int(r.n_steps))
        groups.setdefault(key, []).append(r)
    return groups


def compute_episode_mc_returns(
    rewards: np.ndarray,
    dones: np.ndarray,
    values: np.ndarray,
    last_values: np.ndarray,
    *,
    gamma: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Monte Carlo returns per episode segment (backward), then advantages."""
    n_steps, n_envs = rewards.shape
    returns = np.zeros_like(rewards, dtype=np.float32)
    for env in range(n_envs):
        seg_start = 0
        for t in range(n_steps):
            if not dones[t, env]:
                continue
            g = 0.0
            for k in range(t, seg_start - 1, -1):
                g = float(rewards[k, env]) + gamma * g
                returns[k, env] = g
            seg_start = t + 1
        if seg_start < n_steps:
            g = float(last_values[env])
            for k in range(n_steps - 1, seg_start - 1, -1):
                g = float(rewards[k, env]) + gamma * g
                returns[k, env] = g
    advantages = returns - values
    return returns, advantages


def compute_dual_gamma_mc_returns(
    rewards: np.ndarray,
    rewards_softlock: np.ndarray,
    dones: np.ndarray,
    values: np.ndarray,
    last_values: np.ndarray,
    *,
    gamma_main: float,
    gamma_softlock: float,
) -> tuple[np.ndarray, np.ndarray]:
    """MC returns: main channel @ gamma_main + softlock @ gamma_softlock.

    Softlock channel bootstraps to 0 (terminal lump only; V predicts main stream).
    """
    main = np.asarray(rewards, dtype=np.float32) - np.asarray(
        rewards_softlock, dtype=np.float32
    )
    ret_main, _ = compute_episode_mc_returns(
        main, dones, values, last_values, gamma=gamma_main
    )
    zeros_boot = np.zeros_like(last_values, dtype=np.float32)
    ret_soft, _ = compute_episode_mc_returns(
        np.asarray(rewards_softlock, dtype=np.float32),
        dones,
        values,
        zeros_boot,
        gamma=gamma_softlock,
    )
    returns = ret_main + ret_soft
    advantages = returns - values
    return returns.astype(np.float32, copy=False), advantages.astype(
        np.float32, copy=False
    )


def _normalize_advantages_safe(advantages: np.ndarray) -> np.ndarray:
    """Whitening with population std; single-element batches become zero advantage.

    MaskablePPO re-normalizes per minibatch without SB3's ``len(advantages) > 1``
    guard. A trailing minibatch of size 1 (e.g. 2049 samples @ batch 2048) yields
    NaN std and poisons weights.
    """
    adv = np.asarray(advantages, dtype=np.float32)
    if adv.size <= 1:
        return np.zeros_like(adv, dtype=np.float32)
    mean = float(np.mean(adv))
    std = float(np.std(adv))
    if not np.isfinite(mean) or not np.isfinite(std) or std < 1e-8:
        return (adv - mean).astype(np.float32, copy=False)
    return ((adv - mean) / (std + 1e-8)).astype(np.float32, copy=False)


def _validate_merged_rollout_finite(merged: dict[str, Any]) -> None:
    for key in ("rewards", "values", "log_probs", "last_values", "actions"):
        arr = merged[key]
        if not np.isfinite(arr).all():
            raise ValueError(f"non-finite values in merged {key}")
    for key, arr in merged["obs"].items():
        if np.issubdtype(arr.dtype, np.floating) and not np.isfinite(arr).all():
            raise ValueError(f"non-finite obs[{key!r}]")


def _policy_weights_finite(model: MaskablePPO) -> bool:
    for param in model.policy.parameters():
        if not torch.isfinite(param).all():
            return False
    return True


def _snapshot_policy_state_dict(model: MaskablePPO) -> dict[str, torch.Tensor]:
    return {k: v.detach().cpu().clone() for k, v in model.policy.state_dict().items()}


def _restore_policy_state_dict(model: MaskablePPO, snapshot: dict[str, torch.Tensor]) -> None:
    device = model.device
    restored = {k: v.to(device) for k, v in snapshot.items()}
    model.policy.load_state_dict(restored)



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

    rewards_softlock = np.concatenate(
        [r.softlock_rewards() for r in rollouts], axis=1
    )

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

        "rewards_softlock": rewards_softlock,

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

    from re1_rl.reward import SOFTLOCK_GAMMA

    softlock = merged.get("rewards_softlock")
    if softlock is None:
        softlock = np.zeros_like(merged["rewards"], dtype=np.float32)
    returns_np, advantages_np = compute_dual_gamma_mc_returns(
        merged["rewards"],
        softlock,
        merged["dones"],
        merged["values"],
        merged["last_values"],
        gamma_main=float(model.gamma),
        gamma_softlock=float(SOFTLOCK_GAMMA),
    )
    if not np.isfinite(returns_np).all() or not np.isfinite(advantages_np).all():
        raise ValueError("non-finite MC returns or advantages")
    # SB3 expects numpy (n_steps, n_envs) until swap_and_flatten in buffer.get().
    buffer.returns = returns_np.astype(np.float32, copy=False)
    if getattr(model, "normalize_advantage", False):
        advantages_np = _normalize_advantages_safe(advantages_np)
    buffer.advantages = advantages_np.astype(np.float32, copy=False)
    buffer.generator_ready = False

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

    timesteps = n_steps * n_envs
    if timesteps < 2:
        if machine_name:
            log(
                machine_name,
                f"skip train group policy_version={version} "
                f"n_envs={n_envs} n_steps={n_steps} (<2 samples)",
            )
        merged.clear()
        return 0

    if machine_name:

        log(

            machine_name,

            f"merge_rollouts: policy_version={version} n_envs={n_envs} "

            f"n_steps={n_steps} timesteps={timesteps}",

        )

    _validate_merged_rollout_finite(merged)
    model.rollout_buffer = fill_rollout_buffer(model, merged)

    ensure_training_logger(model)

    saved_norm_adv = bool(getattr(model, "normalize_advantage", False))
    model.normalize_advantage = False
    weight_snapshot = _snapshot_policy_state_dict(model)
    try:
        model.train()
        if not _policy_weights_finite(model):
            raise RuntimeError("policy weights non-finite after train()")
    except Exception:
        _restore_policy_state_dict(model, weight_snapshot)
        raise
    finally:
        model.normalize_advantage = saved_norm_adv

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

    current_policy_version: int | None = None,

    max_staleness: int = 1,

    relevance_gate: bool = False,

    relevance_config: Any | None = None,

    learner_state: Any | None = None,

) -> int:

    """Train PPO on rollouts, grouped by policy_version and rollout length.

    When ``relevance_gate`` is True and ``current_policy_version`` is set, rollouts
    older than ``current - max_staleness`` are kept only if π_new still owns enough
    of the logged actions (see ``relevance_gate`` module). Fresh rollouts pass
    through unchanged. NaN / non-finite ratios fail closed at the transition level.
    """

    if not rollouts:

        return 0

    if relevance_gate and current_policy_version is not None:

        from re1_rl.distributed.relevance_gate import (

            RelevanceGateConfig,

            filter_stale_rollouts,

        )



        cfg = relevance_config or RelevanceGateConfig()

        before = len(rollouts)

        kept, gate_stats, _details = filter_stale_rollouts(

            model,

            rollouts,

            current_policy_version=int(current_policy_version),

            max_staleness=int(max_staleness),

            config=cfg,

        )

        if learner_state is not None and hasattr(learner_state, "record_relevance_stats"):

            learner_state.record_relevance_stats(

                kept=gate_stats.kept,

                dropped=gate_stats.dropped,

                steps_kept=gate_stats.steps_kept,

                steps_dropped=gate_stats.steps_dropped,

            )

        if machine_name and gate_stats.considered:

            step_total = gate_stats.steps_kept + gate_stats.steps_dropped

            step_keep = (
                float(gate_stats.steps_kept) / float(step_total) if step_total else 0.0
            )

            log(

                machine_name,

                "relevance_gate: "

                f"considered={gate_stats.considered} kept={gate_stats.kept} "

                f"dropped={gate_stats.dropped} "

                f"steps_kept={gate_stats.steps_kept} "

                f"steps_dropped={gate_stats.steps_dropped} "

                f"step_keep_rate={step_keep:.3f} "

                f"tx_pass={gate_stats.transitions_pass}/"

                f"{gate_stats.transitions_total} "

                f"keep_rate={gate_stats.as_dict()['relevance_keep_rate']:.3f} "

                f"(batch {before}->{len(kept)})",

            )

        dropped = [r for r in rollouts if id(r) not in {id(x) for x in kept}]

        if dropped:

            _release_rollout_arrays(dropped)

        rollouts = kept

        if not rollouts:

            return 0

    groups = group_rollouts_for_train(rollouts)

    total = 0

    try:

        for key in sorted(groups):

            total += _train_one_version(

                model,

                groups[key],

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

            trained_steps = train_on_rollouts(
                model,
                pending,
                machine_name=machine_name,
                current_policy_version=int(
                    getattr(learner_state, "current_policy_version", 0) or 0
                ),
                max_staleness=int(getattr(learner_state, "max_staleness", 1) or 1),
                relevance_gate=bool(getattr(learner_state, "relevance_gate", False)),
                learner_state=learner_state,
            )

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


