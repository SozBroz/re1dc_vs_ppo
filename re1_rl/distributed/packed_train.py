"""Pack accepted rollouts into one sample-weighted PPO update."""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.buffers import MaskableDictRolloutBuffer

from re1_rl.distributed.log_util import log
from re1_rl.distributed.rollout_types import WorkerRollout, normalize_curriculum_id
from re1_rl.loadout_learning import (
    apply_bounded_loadout_guidance,
    loadout_samples_from_infos,
)
from re1_rl.training_metrics_log import ensure_training_logger


def filter_rollouts_by_identity(
    rollouts: list[WorkerRollout],
    *,
    expected_curriculum_id: str = "",
    expected_obs_schema_version: int | None = None,
    machine_name: str = "",
) -> list[WorkerRollout]:
    """Drop curriculum/schema mismatches (fail closed when expectations set)."""
    want_cur = normalize_curriculum_id(expected_curriculum_id)
    kept: list[WorkerRollout] = []
    dropped = 0
    for r in rollouts:
        if want_cur:
            cid = normalize_curriculum_id(r.curriculum_id)
            if not cid or cid != want_cur:
                dropped += 1
                continue
        if expected_obs_schema_version is not None:
            if int(r.obs_schema_version or 0) != int(expected_obs_schema_version):
                dropped += 1
                continue
        kept.append(r)
    if dropped and machine_name:
        log(
            machine_name,
            f"identity_filter: dropped={dropped} kept={len(kept)} "
            f"curriculum={want_cur!r} schema={expected_obs_schema_version!r}",
        )
    return kept


def _swap_flatten_time_major(arr: np.ndarray) -> np.ndarray:
    """(n_steps, n_envs, ...) -> (n_steps * n_envs, ...) matching SB3 buffer order."""
    a = np.asarray(arr)
    if a.ndim < 2:
        raise ValueError(f"expected at least 2 dims, got {a.shape}")
    n_steps, n_envs = int(a.shape[0]), int(a.shape[1])
    rest = a.shape[2:]
    return np.swapaxes(a, 0, 1).reshape((n_envs * n_steps, *rest))


def _merged_to_flat_segment(model: MaskablePPO, merged: dict[str, Any]) -> dict[str, Any]:
    from re1_rl.distributed.learner_train import (
        _validate_merged_rollout_finite,
        compute_episode_mc_returns,
    )

    _validate_merged_rollout_finite(merged)
    returns_np, advantages_np = compute_episode_mc_returns(
        merged["rewards"],
        merged["dones"],
        merged["values"],
        merged["last_values"],
        gamma=float(model.gamma),
    )
    if not np.isfinite(returns_np).all() or not np.isfinite(advantages_np).all():
        raise ValueError("non-finite MC returns or advantages")
    return {
        "obs": {k: _swap_flatten_time_major(merged["obs"][k]) for k in merged["obs"]},
        "actions": _swap_flatten_time_major(merged["actions"]),
        "rewards": _swap_flatten_time_major(merged["rewards"]),
        "episode_starts": _swap_flatten_time_major(merged["episode_starts"]),
        "values": _swap_flatten_time_major(merged["values"]),
        "log_probs": _swap_flatten_time_major(merged["log_probs"]),
        "action_masks": _swap_flatten_time_major(merged["action_masks"]),
        "returns": _swap_flatten_time_major(returns_np),
        "advantages": _swap_flatten_time_major(advantages_np),
        "combat_targets": _swap_flatten_time_major(merged["combat_targets"]),
        "world_event_targets": _swap_flatten_time_major(merged["world_event_targets"]),
        "world_event_masks": _swap_flatten_time_major(merged["world_event_masks"]),
        "mod_drop_masks": (
            _swap_flatten_time_major(merged["mod_drop_masks"])
            if merged.get("mod_drop_masks") is not None
            else None
        ),
        "n": int(merged["n_steps"]) * int(merged["n_envs"]),
    }


def _concat_flat_segments(segments: list[dict[str, Any]]) -> dict[str, Any]:
    if not segments:
        raise ValueError("empty segment list")
    obs_keys = segments[0]["obs"].keys()
    return {
        "obs": {
            k: np.concatenate([s["obs"][k] for s in segments], axis=0) for k in obs_keys
        },
        "actions": np.concatenate([s["actions"] for s in segments], axis=0),
        "rewards": np.concatenate([s["rewards"] for s in segments], axis=0),
        "episode_starts": np.concatenate(
            [s["episode_starts"] for s in segments], axis=0
        ),
        "values": np.concatenate([s["values"] for s in segments], axis=0),
        "log_probs": np.concatenate([s["log_probs"] for s in segments], axis=0),
        "action_masks": np.concatenate([s["action_masks"] for s in segments], axis=0),
        "returns": np.concatenate([s["returns"] for s in segments], axis=0),
        "advantages": np.concatenate([s["advantages"] for s in segments], axis=0),
        "combat_targets": np.concatenate(
            [s["combat_targets"] for s in segments], axis=0
        ),
        "world_event_targets": np.concatenate(
            [s["world_event_targets"] for s in segments], axis=0
        ),
        "world_event_masks": np.concatenate(
            [s["world_event_masks"] for s in segments], axis=0
        ),
        "mod_drop_masks": (
            np.concatenate([s["mod_drop_masks"] for s in segments], axis=0)
            if segments[0].get("mod_drop_masks") is not None
            and all(s.get("mod_drop_masks") is not None for s in segments)
            else None
        ),
        "n": int(sum(s["n"] for s in segments)),
    }


def fill_packed_rollout_buffer(
    model: MaskablePPO,
    flat: dict[str, Any],
) -> MaskableDictRolloutBuffer:
    """Fill a (n_steps=N, n_envs=1) buffer from flattened fleet samples."""
    from re1_rl.distributed.learner_train import (
        _normalize_advantages_safe,
        _obs_step_for_buffer,
    )

    n = int(flat["n"])
    buffer = MaskableDictRolloutBuffer(
        n,
        model.observation_space,
        model.action_space,
        device=model.device,
        gamma=model.gamma,
        gae_lambda=model.gae_lambda,
        n_envs=1,
    )
    buffer.reset()
    for i in range(n):
        obs_step = _obs_step_for_buffer(
            {k: flat["obs"][k][i : i + 1] for k in flat["obs"]},
            model.observation_space,
        )
        buffer.add(
            obs_step,
            flat["actions"][i : i + 1],
            flat["rewards"][i : i + 1],
            flat["episode_starts"][i : i + 1],
            torch.as_tensor(flat["values"][i : i + 1], device=model.device),
            torch.as_tensor(flat["log_probs"][i : i + 1], device=model.device),
            action_masks=flat["action_masks"][i : i + 1],
        )
    returns = np.asarray(flat["returns"], dtype=np.float32).reshape(n, 1)
    advantages = np.asarray(flat["advantages"], dtype=np.float32).reshape(-1)
    advantages = _normalize_advantages_safe(advantages).reshape(n, 1)
    buffer.returns = returns
    buffer.advantages = advantages
    buffer.generator_ready = False
    return buffer


def _fill_pack_metrics(
    fleet_metrics: dict[str, Any] | None,
    *,
    accepted_steps: int,
    rollouts: list[WorkerRollout],
    version_counts: dict[int, int] | None = None,
) -> None:
    if fleet_metrics is None:
        return
    from re1_rl.training_metrics_log import (
        curriculum_id_from_rollouts,
        policy_version_counts as _version_counts,
        unique_contributor_machines,
    )

    counts = version_counts or {}
    fleet_metrics.update(
        {
            "accepted_steps": int(accepted_steps),
            "contributors": unique_contributor_machines(rollouts),
            "curriculum_id": curriculum_id_from_rollouts(rollouts),
            "policy_version_counts": {
                str(k): int(v) for k, v in sorted(counts.items())
            }
            if counts
            else _version_counts(rollouts),
        }
    )


def train_packed_on_rollouts(
    model: MaskablePPO,
    rollouts: list[WorkerRollout],
    *,
    machine_name: str = "",
    fleet_metrics: dict[str, Any] | None = None,
) -> int:
    """One sample-weighted ``model.train()`` over all accepted rollouts."""
    from re1_rl.distributed.learner_train import (
        _policy_weights_finite,
        _restore_policy_state_dict,
        _snapshot_policy_state_dict,
        group_rollouts_for_train,
        merge_rollouts,
    )

    if not rollouts:
        _fill_pack_metrics(fleet_metrics, accepted_steps=0, rollouts=[])
        return 0

    groups = group_rollouts_for_train(rollouts)
    loadout_samples = loadout_samples_from_infos(
        info for r in rollouts for info in (r.episode_infos or [])
    )
    loadout_stats: dict[str, float] = {}
    if hasattr(model, "prepare_loadout_epoch"):
        loadout_stats = model.prepare_loadout_epoch(loadout_samples)

    segments: list[dict[str, Any]] = []
    version_counts: dict[int, int] = {}
    guidance_transfers = 0.0
    guidance_total = 0.0
    for key in sorted(groups):
        group = groups[key]
        merged = merge_rollouts(group)
        n_seg = int(merged["n_steps"]) * int(merged["n_envs"])
        version_counts[int(merged["policy_version"])] = (
            version_counts.get(int(merged["policy_version"]), 0) + n_seg
        )
        if hasattr(model, "frozen_loadout_scorer"):
            guidance = apply_bounded_loadout_guidance(
                merged,
                model.frozen_loadout_scorer,
                device=torch.device(model.device),
                calibrated=bool(getattr(model, "loadout_calibrated", False)),
            )
            guidance_transfers += float(guidance.get("transfers", 0.0))
            guidance_total += float(guidance.get("total", 0.0))
        segments.append(_merged_to_flat_segment(model, merged))

    loadout_stats = {
        **loadout_stats,
        "guidance_transfers": guidance_transfers,
        "guidance_total": guidance_total,
    }
    flat = _concat_flat_segments(segments)
    n = int(flat["n"])
    if n < 2:
        if machine_name:
            log(machine_name, f"skip packed train (<2 samples, n={n})")
        _fill_pack_metrics(
            fleet_metrics,
            accepted_steps=0,
            rollouts=rollouts,
            version_counts=version_counts,
        )
        return 0

    if machine_name:
        log(
            machine_name,
            f"packed_train: samples={n} groups={len(groups)} "
            f"versions={dict(sorted(version_counts.items()))}",
        )

    ensure_training_logger(model)
    if getattr(model, "logger", None) is not None:
        for key, value in loadout_stats.items():
            model.logger.record(f"train/loadout_{key}", float(value))

    saved_norm_adv = bool(getattr(model, "normalize_advantage", False))
    model.rollout_buffer = fill_packed_rollout_buffer(model, flat)
    # Advantages already whitened fleet-wide; block per-minibatch re-norm.
    model.normalize_advantage = False

    if hasattr(model, "set_auxiliary_targets"):
        # 2-D flats already in SB3 swap_and_flatten order (no further reshape).
        model.set_auxiliary_targets(
            np.asarray(flat["combat_targets"], dtype=np.float32),
            np.asarray(flat["world_event_targets"], dtype=np.float32),
            np.asarray(flat["world_event_masks"], dtype=np.float32),
        )
    if hasattr(model, "set_mod_drop_masks"):
        md = flat.get("mod_drop_masks")
        model.set_mod_drop_masks(
            None if md is None else np.asarray(md, dtype=np.float32)
        )

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
        if hasattr(model, "set_auxiliary_targets"):
            model.set_auxiliary_targets(None, None, None)
        if hasattr(model, "set_mod_drop_masks"):
            model.set_mod_drop_masks(None)

    model.num_timesteps += int(n)
    if getattr(model, "logger", None) is not None:
        model.logger.record("train/packed_samples", float(n))
        model.logger.record("train/packed_groups", float(len(groups)))
        # Snapshot before dump — SB3 clears name_to_value on dump.
        if fleet_metrics is not None:
            from re1_rl.training_metrics_log import extract_logger_scalars

            fleet_metrics["logger_scalars"] = extract_logger_scalars(model)
        model.logger.dump(step=int(model.num_timesteps))
    _fill_pack_metrics(
        fleet_metrics,
        accepted_steps=n,
        rollouts=rollouts,
        version_counts=version_counts,
    )
    return int(n)
