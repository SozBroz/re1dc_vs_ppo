"""Persist PPO / training scalars for offline analysis (async + sync)."""

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


def log_update_line(record: dict[str, Any]) -> None:
    """One-line console summary of the most useful PPO heuristics."""
    parts = [
        f"update={record.get('update')}",
        f"steps={record.get('num_timesteps')}",
        f"rate={record.get('rate_steps_s', 0):.1f}/s",
    ]
    for key in (
        "train/approx_kl",
        "train/clip_fraction",
        "train/explained_variance",
        "train/entropy_loss",
        "train/value_loss",
        "rollout/ep_rew_mean",
        "batch/reward_mean",
    ):
        if key in record:
            parts.append(f"{key.split('/')[-1]}={record[key]:.4g}")
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
