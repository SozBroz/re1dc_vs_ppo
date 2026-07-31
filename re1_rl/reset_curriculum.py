"""Unified reset source sampler: fresh | PB | Go-Explore archive."""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Literal

ResetSource = Literal["fresh", "pb", "archive"]
ResetMixSource = Literal["fresh", "focus_pb", "other_pb", "archive"]


def archive_weight_from_env(default: float = 0.0) -> float:
    """``RE1_GO_EXPLORE_RESET_WEIGHT`` in ``[0, 1]``; default 0 (shadow / Phase C)."""
    raw = os.environ.get("RE1_GO_EXPLORE_RESET_WEIGHT", "").strip()
    if not raw:
        return float(default)
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return float(default)


def _weight_from_env(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def focus_room_from_env(default: str = "") -> str:
    """``RE1_RESET_FOCUS_ROOM`` — when set, enables the 4-way reset mix."""
    raw = os.environ.get("RE1_RESET_FOCUS_ROOM", "").strip()
    if not raw:
        return str(default).strip().upper()
    return raw.strip().upper()


@dataclass(frozen=True)
class ResetMixWeights:
    """Four-way reset mix (must sum to ~1 when all sources are available)."""

    fresh: float = 0.30
    focus_pb: float = 0.30
    other_pb: float = 0.30
    archive: float = 0.10

    def normalized(self) -> ResetMixWeights:
        total = self.fresh + self.focus_pb + self.other_pb + self.archive
        if total <= 0.0:
            return ResetMixWeights(1.0, 0.0, 0.0, 0.0)
        return ResetMixWeights(
            self.fresh / total,
            self.focus_pb / total,
            self.other_pb / total,
            self.archive / total,
        )


def reset_mix_from_env() -> ResetMixWeights | None:
    """Load 4-way mix weights when ``RE1_RESET_FOCUS_ROOM`` is set.

    Optional overrides: ``RE1_RESET_MIX_FRESH``, ``RE1_RESET_MIX_FOCUS_PB``,
    ``RE1_RESET_MIX_OTHER_PB``, ``RE1_RESET_MIX_ARCHIVE``.
    ``RE1_GO_EXPLORE_RESET_WEIGHT`` fills ``archive`` when ``RE1_RESET_MIX_ARCHIVE``
    is unset.
    """
    focus_room = focus_room_from_env()
    if not focus_room:
        return None

    fresh = _weight_from_env("RE1_RESET_MIX_FRESH")
    focus_pb = _weight_from_env("RE1_RESET_MIX_FOCUS_PB")
    other_pb = _weight_from_env("RE1_RESET_MIX_OTHER_PB")
    archive = _weight_from_env("RE1_RESET_MIX_ARCHIVE")
    if archive is None:
        archive = archive_weight_from_env(default=0.10)

    mix = ResetMixWeights(
        fresh=0.30 if fresh is None else fresh,
        focus_pb=0.30 if focus_pb is None else focus_pb,
        other_pb=0.30 if other_pb is None else other_pb,
        archive=archive,
    )
    return mix.normalized()


def sample_reset_source(
    rng: random.Random,
    *,
    archive_weight: float,
    pb_weight: float,
) -> ResetSource:
    """Sample reset source for the legacy 3-way Yawn canary mix.

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


def sample_reset_mix(
    rng: random.Random,
    weights: ResetMixWeights,
    *,
    focus_pb_available: bool,
    other_pb_available: bool,
    archive_available: bool,
) -> ResetMixSource:
    """Sample a 4-way reset source; unavailable buckets are renormalized away."""
    w = weights.normalized()
    buckets: list[tuple[ResetMixSource, float]] = [("fresh", w.fresh)]
    if focus_pb_available:
        buckets.append(("focus_pb", w.focus_pb))
    if other_pb_available:
        buckets.append(("other_pb", w.other_pb))
    if archive_available:
        buckets.append(("archive", w.archive))
    total = sum(weight for _, weight in buckets)
    if total <= 0.0:
        return "fresh"
    u = rng.random() * total
    acc = 0.0
    for name, weight in buckets:
        acc += weight
        if u < acc:
            return name
    return buckets[-1][0]
