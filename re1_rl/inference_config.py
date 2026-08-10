"""Rollout inference knobs (temperature, experiment tags)."""

from __future__ import annotations

import os


def inference_temperature_from_env(*, default: float = 1.0) -> float:
    """``RE1_INFERENCE_TEMPERATURE`` — softmax sharpening at collect time (default 1.0)."""
    raw = os.environ.get("RE1_INFERENCE_TEMPERATURE", "").strip()
    if not raw:
        return float(default)
    try:
        temp = float(raw)
    except ValueError:
        return float(default)
    # Avoid div-by-zero; cap extreme sharpening/flattening.
    return max(0.05, min(2.0, temp))


def grouped_entropy_training_from_env() -> bool:
    """``RE1_USE_GROUPED_ENTROPY=1`` — learner training loss only (not inference)."""
    raw = os.environ.get("RE1_USE_GROUPED_ENTROPY", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def memlog_experiment_metadata() -> dict[str, object]:
    """Tags published in memlog telemetry for offline A/B joins."""
    experiment_id = os.environ.get("RE1_MEMLOG_EXPERIMENT", "").strip()
    eval_only = os.environ.get("RE1_EVAL_ONLY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return {
        "experiment_id": experiment_id or None,
        "inference_temperature": inference_temperature_from_env(),
        "policy_train_variant": (
            "grouped_entropy" if grouped_entropy_training_from_env() else "baseline"
        ),
        "eval_only": bool(eval_only),
    }


def memlog_fight_index_from_env(*, default: int = 18) -> int:
    """``RE1_MEMLOG_FIGHT_INDEX`` — fight CP scored in memlog episodes (default 18)."""
    raw = os.environ.get("RE1_MEMLOG_FIGHT_INDEX", "").strip()
    if not raw:
        return int(default)
    try:
        return max(0, int(raw))
    except ValueError:
        return int(default)


def memlog_max_episodes_from_env() -> int | None:
    """``RE1_MEMLOG_MAX_EPISODES`` — auto-shutdown memlog after N episodes."""
    raw = os.environ.get("RE1_MEMLOG_MAX_EPISODES", "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None
