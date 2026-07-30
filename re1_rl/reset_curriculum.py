"""Unified reset source sampler: fresh | PB | Go-Explore archive."""

from __future__ import annotations

import os
import random
from typing import Literal

ResetSource = Literal["fresh", "pb", "archive"]


def archive_weight_from_env(default: float = 0.0) -> float:
    """``RE1_GO_EXPLORE_RESET_WEIGHT`` in ``[0, 1]``; default 0 (shadow / Phase C)."""
    raw = os.environ.get("RE1_GO_EXPLORE_RESET_WEIGHT", "").strip()
    if not raw:
        return float(default)
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return float(default)


def sample_reset_source(
    rng: random.Random,
    *,
    archive_weight: float,
    pb_weight: float,
) -> ResetSource:
    """Sample reset source for the Yawn canary mix.

    ``P(archive) = archive_weight``
    Among the remainder, ``P(pb) = pb_weight`` and ``P(fresh) = 1 - pb_weight``.

    So overall:
      P(archive) = archive_weight
      P(pb)      = (1 - archive_weight) * pb_weight
      P(fresh)   = (1 - archive_weight) * (1 - pb_weight)
    """
    aw = max(0.0, min(1.0, float(archive_weight)))
    pw = max(0.0, min(1.0, float(pb_weight)))
    u = rng.random()
    if u < aw:
        return "archive"
    if rng.random() < pw:
        return "pb"
    return "fresh"
