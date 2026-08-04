"""Env/config flags for modality diagnostics, FiLM, and ablations.

All flags default OFF so fleet behavior is unchanged unless explicitly enabled.
"""

from __future__ import annotations

import os


def _env_truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def modality_diag_enabled() -> bool:
    """Log per-tower utilization diagnostics (periodic, not every minibatch)."""
    return _env_truthy("RE1_MODALITY_DIAG", "0")


def modality_diag_every_n_updates() -> int:
    """Run diagnostics every N PPO ``_n_updates`` (default 1 = each train())."""
    return max(1, _env_int("RE1_MODALITY_DIAG_EVERY", 1))


def goal_film_enabled() -> bool:
    """Identity-init FiLM goal conditioning on vision/spatial towers."""
    return _env_truthy("RE1_GOAL_FILM", "0")


def mod_drop_enabled() -> bool:
    """Policy-consistent structured modality dropout (stored masks)."""
    return _env_truthy("RE1_MOD_DROP", "0")


def mod_drop_rate() -> float:
    """Probability an episode/segment drops one non-goal branch (default 0.05)."""
    return min(1.0, max(0.0, _env_float("RE1_MOD_DROP_RATE", 0.05)))


def discriminative_lr_enabled() -> bool:
    """Mature towers (CNN/world) use a reduced learning rate."""
    return _env_truthy("RE1_DISC_LR", "0")


def discriminative_lr_mult() -> float:
    """Multiplier for mature-tower LR (default 0.2)."""
    return min(1.0, max(0.01, _env_float("RE1_DISC_LR_MULT", 0.2)))
