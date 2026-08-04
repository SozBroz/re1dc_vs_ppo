"""Persist PPO / training scalars for offline analysis (async + sync).

Fleet epoch (Baseline E): after one packed ``model.train()``, emit exactly one
``[train:metrics]`` line and one JSONL record via ``emit_fleet_epoch_metrics``.

Zero-N policy: when a fleet epoch train is attempted but no samples reach
``train()`` (empty after relevance/identity, or ``n < 2``), still emit one
record/line with ``accepted_steps=0`` and zeroed PPO counters — never reuse
stale logger scalars from a prior update. Empty-pending grace restarts that
never enter the train path do not emit.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stable_baselines3 import PPO

# SB3 keys we expect after ``model.train()`` / ``model.learn()`` rollouts.
PPO_TRAIN_TAGS = (
    "train/loss",
    "train/entropy_loss",
    "train/policy_gradient_loss",
    "train/value_loss",
    "train/approx_kl",
    "train/clip_fraction",
    "train/clip_range",
    "train/explained_variance",
    "train/learning_rate",
    "train/n_updates",
    "train/std",
    "train/aux_loss",
    "train/aux_coef",
    "train/aux_combat_bce",
    "train/aux_combat_huber",
    "train/aux_world_bce",
    "train/grouped_entropy",
    "train/engage_prob",
    "train/conditional_combat_entropy",
    "train/optimizer_steps",
    "train/n_epochs_completed",
    "train/effective_batch",
    "train/early_stop",
)

# Fleet-record keys written beside logger scalars (Baseline E).
FLEET_EPOCH_KEYS = (
    "accepted_steps",
    "contributors",
    "n_contributors",
    "curriculum_id",
    "policy_version_lag_hist",
    "policy_version_counts",
    "collection_wall_s",
    "train_wall_s",
    "relevance_keep_rate",
    "relevance_step_keep_rate",
)

ROLLOUT_TAGS = (
    "rollout/ep_rew_mean",
    "rollout/ep_len_mean",
)


def training_metrics_jsonl_path(
    project_root: Path,
    *,
    run_name: str | None = None,
) -> Path:
    logs = project_root / "logs"
    if run_name:
        return logs / f"training_metrics_{run_name}.jsonl"
    return logs / "training_metrics.jsonl"


def configure_training_logger(
    model: PPO,
    *,
    log_dir: str | Path,
    formats: tuple[str, ...] = ("stdout", "tensorboard", "csv"),
) -> None:
    """Attach SB3 logger (tensorboard + csv under ``log_dir``)."""
    from stable_baselines3.common.logger import configure

    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    model.set_logger(configure(str(path), list(formats)))


def ensure_training_logger(model: PPO, *, log_dir: str | Path | None = None) -> None:
    """Guarantee SB3 logger exists before ``model.train()`` (records scalars)."""
    if getattr(model, "_logger", None) is not None:
        return
    if log_dir is not None:
        configure_training_logger(model, log_dir=log_dir)
        return
    from stable_baselines3.common.logger import configure

    model.set_logger(configure(folder=None, format_strings=[]))


def extract_logger_scalars(model: PPO) -> dict[str, float]:
    """Snapshot numeric values from the SB3 logger after train/learn."""
    logger = getattr(model, "_logger", None)
    if logger is None:
        return {}
    out: dict[str, float] = {}
    for key, val in logger.name_to_value.items():
        if isinstance(val, (bool, int, float)):
            out[str(key)] = float(val)
    return out


def rollout_batch_reward_stats(rollouts: list[Any]) -> dict[str, float]:
    """Mean/min/max reward over a merged async rollout batch."""
    import numpy as np

    if not rollouts:
        return {}
    means = [float(np.mean(r.rewards)) for r in rollouts]
    return {
        "batch/reward_mean": float(np.mean(means)),
        "batch/reward_min": float(np.min(means)),
        "batch/reward_max": float(np.max(means)),
    }


def append_training_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def unique_contributor_machines(rollouts: list[Any]) -> list[str]:
    """Sorted unique machine ids from rollout ``worker_id`` (strip ``:actor_N``)."""
    from re1_rl.distributed.learner_server import base_worker_id

    machines: set[str] = set()
    for r in rollouts:
        wid = getattr(r, "worker_id", None)
        if wid is None:
            continue
        machines.add(base_worker_id(str(wid)))
    return sorted(machines)


def curriculum_id_from_rollouts(rollouts: list[Any]) -> str:
    """Single curriculum id if unanimous; else empty (mixed/missing)."""
    from re1_rl.distributed.rollout_types import normalize_curriculum_id

    ids = {
        normalize_curriculum_id(getattr(r, "curriculum_id", "") or "")
        for r in rollouts
    }
    ids.discard("")
    if len(ids) == 1:
        return next(iter(ids))
    return ""


def policy_version_counts(rollouts: list[Any]) -> dict[str, int]:
    """Accepted env-steps keyed by rollout ``policy_version`` (string keys)."""
    counts: dict[str, int] = {}
    for r in rollouts:
        key = str(int(getattr(r, "policy_version", 0) or 0))
        n = int(r.num_timesteps()) if hasattr(r, "num_timesteps") else 0
        counts[key] = counts.get(key, 0) + n
    return dict(sorted(counts.items(), key=lambda kv: int(kv[0])))


def policy_version_lag_hist(
    rollouts: list[Any],
    *,
    current_policy_version: int,
) -> dict[str, int]:
    """Env-steps by lag ``current - rollout.policy_version`` (string keys)."""
    hist: dict[str, int] = {}
    cur = int(current_policy_version)
    for r in rollouts:
        lag = cur - int(getattr(r, "policy_version", 0) or 0)
        n = int(r.num_timesteps()) if hasattr(r, "num_timesteps") else 0
        key = str(lag)
        hist[key] = hist.get(key, 0) + n
    return dict(sorted(hist.items(), key=lambda kv: int(kv[0])))


def format_lag_hist(hist: dict[str, Any] | None) -> str:
    """Compact ``lag0=12k,lag1=3`` for console lines."""
    if not hist:
        return ""
    parts: list[str] = []
    for lag in sorted(hist, key=lambda k: int(k)):
        parts.append(f"lag{lag}={int(hist[lag])}")
    return ",".join(parts)


def build_update_record(
    model: PPO,
    *,
    update: int,
    policy_version: int,
    rate_steps_s: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "update": int(update),
        "policy_version": int(policy_version),
        "num_timesteps": int(model.num_timesteps),
        "rate_steps_s": float(rate_steps_s),
    }
    record.update(extract_logger_scalars(model))
    if extra:
        record.update(extra)
    return record


def build_fleet_epoch_record(
    model: PPO | None,
    *,
    update: int,
    policy_version: int,
    accepted_steps: int,
    contributors: list[str] | None = None,
    curriculum_id: str = "",
    collection_wall_s: float = 0.0,
    train_wall_s: float = 0.0,
    policy_version_lag_hist: dict[str, int] | None = None,
    policy_version_counts: dict[str, int] | None = None,
    relevance_keep_rate: float | None = None,
    relevance_step_keep_rate: float | None = None,
    rate_steps_s: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One fleet-epoch metrics record (post packed train, or zero-N).

    When ``accepted_steps == 0``, logger scalars are not copied (avoids stale
    KL/loss from the previous update); PPO counters are written as zeros.
    """
    contrib = list(contributors or [])
    fleet_extra: dict[str, Any] = {
        "accepted_steps": int(accepted_steps),
        "contributors": contrib,
        "n_contributors": len(contrib),
        "curriculum_id": str(curriculum_id or ""),
        "policy_version_lag_hist": dict(policy_version_lag_hist or {}),
        "policy_version_counts": dict(policy_version_counts or {}),
        "collection_wall_s": float(collection_wall_s),
        "train_wall_s": float(train_wall_s),
    }
    if relevance_keep_rate is not None:
        fleet_extra["relevance_keep_rate"] = float(relevance_keep_rate)
    if relevance_step_keep_rate is not None:
        fleet_extra["relevance_step_keep_rate"] = float(relevance_step_keep_rate)
    if extra:
        fleet_extra.update(extra)

    # Prefer pre-dump snapshot from packed train (SB3 dump clears name_to_value).
    logger_scalars = fleet_extra.pop("logger_scalars", None)

    if int(accepted_steps) > 0:
        record: dict[str, Any] = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "update": int(update),
            "policy_version": int(policy_version),
            "num_timesteps": int(getattr(model, "num_timesteps", 0) or 0),
            "rate_steps_s": float(rate_steps_s),
        }
        if isinstance(logger_scalars, dict) and logger_scalars:
            record.update(logger_scalars)
        elif model is not None:
            record.update(extract_logger_scalars(model))
        record.update(fleet_extra)
        return record

    record = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "update": int(update),
        "policy_version": int(policy_version),
        "num_timesteps": int(getattr(model, "num_timesteps", 0) or 0),
        "rate_steps_s": float(rate_steps_s),
        "train/optimizer_steps": 0.0,
        "train/n_epochs_completed": 0.0,
        "train/effective_batch": 0.0,
        "train/early_stop": 0.0,
    }
    record.update(fleet_extra)
    return record


def emit_fleet_epoch_metrics(
    path: Path | None,
    record: dict[str, Any],
) -> None:
    """Append one JSONL line (optional) and print one ``[train:metrics]`` line."""
    if path is not None:
        append_training_record(path, record)
    log_update_line(record)


def log_update_line(record: dict[str, Any]) -> None:
    """One-line console summary of the most useful PPO / fleet heuristics."""
    parts = [
        f"update={record.get('update')}",
        f"steps={record.get('num_timesteps')}",
        f"rate={record.get('rate_steps_s', 0):.1f}/s",
    ]
    if "accepted_steps" in record:
        parts.append(f"accepted={record['accepted_steps']}")
    if "n_contributors" in record:
        parts.append(f"contrib={record['n_contributors']}")
    if record.get("curriculum_id"):
        parts.append(f"curriculum={record['curriculum_id']}")
    for key in (
        "train/approx_kl",
        "train/clip_fraction",
        "train/explained_variance",
        "train/entropy_loss",
        "train/value_loss",
        "train/loss",
        "train/optimizer_steps",
        "train/n_epochs_completed",
        "train/effective_batch",
        "rollout/ep_rew_mean",
        "batch/reward_mean",
    ):
        if key in record:
            val = record[key]
            if isinstance(val, (int, float)):
                parts.append(f"{key.split('/')[-1]}={float(val):.4g}")
    lag = format_lag_hist(record.get("policy_version_lag_hist"))
    if lag:
        parts.append(f"ver_lag={lag}")
    if "collection_wall_s" in record:
        parts.append(f"collect_s={float(record['collection_wall_s']):.1f}")
    if "train_wall_s" in record:
        parts.append(f"train_s={float(record['train_wall_s']):.1f}")
    if "relevance_step_keep_rate" in record:
        parts.append(
            f"rel_step_keep={float(record['relevance_step_keep_rate']):.3f}"
        )
    elif "relevance_keep_rate" in record:
        parts.append(f"rel_keep={float(record['relevance_keep_rate']):.3f}")
    print(f"[train:metrics] {' '.join(parts)}", flush=True)


class TrainingMetricsJsonlCallback:
    """SB3 callback: append PPO train scalars to JSONL each rollout."""

    def __init__(self, jsonl_path: Path) -> None:
        from stable_baselines3.common.callbacks import BaseCallback

        path = jsonl_path
        state = {"update": 0, "t0": time.perf_counter()}

        class _Cb(BaseCallback):
            def _on_step(self) -> bool:
                return True

            def _on_rollout_end(self) -> bool:
                state["update"] += 1
                elapsed = time.perf_counter() - state["t0"]
                steps = int(self.model.num_timesteps)
                rate = steps / elapsed if elapsed > 0 else 0.0
                record = build_update_record(
                    self.model,
                    update=state["update"],
                    policy_version=state["update"],
                    rate_steps_s=rate,
                )
                append_training_record(path, record)
                log_update_line(record)
                return True

        self._callback: BaseCallback = _Cb()

    def get_callback(self):
        return self._callback
