"""Reset curriculum mix weights (fresh | PB | archive)."""

from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.reset_curriculum import (
    ResetMixWeights,
    archive_weight_from_env,
    focus_room_from_env,
    reset_mix_from_env,
    sample_reset_mix,
    sample_reset_source,
)


def test_archive_weight_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RE1_GO_EXPLORE_RESET_WEIGHT", raising=False)
    assert archive_weight_from_env(0.0) == 0.0
    monkeypatch.setenv("RE1_GO_EXPLORE_RESET_WEIGHT", "0.05")
    assert archive_weight_from_env() == pytest.approx(0.05)
    monkeypatch.setenv("RE1_GO_EXPLORE_RESET_WEIGHT", "2.0")
    assert archive_weight_from_env() == 1.0


def test_sample_always_archive_when_weight_one() -> None:
    rng = random.Random(0)
    for _ in range(40):
        assert sample_reset_source(rng, archive_weight=1.0, pb_weight=0.5) == "archive"


def test_sample_never_archive_when_weight_zero() -> None:
    rng = random.Random(1)
    for _ in range(40):
        src = sample_reset_source(rng, archive_weight=0.0, pb_weight=1.0)
        assert src in ("pb", "fresh")
        assert src == "pb"


def test_sample_always_fresh_when_both_zero() -> None:
    rng = random.Random(2)
    for _ in range(40):
        assert sample_reset_source(rng, archive_weight=0.0, pb_weight=0.0) == "fresh"


def test_sample_mix_distribution() -> None:
    """Rough mass check: archive≈0.2, of remainder pb≈0.5 → pb≈0.4, fresh≈0.4."""
    rng = random.Random(42)
    counts: Counter[str] = Counter()
    n = 5000
    for _ in range(n):
        counts[sample_reset_source(rng, archive_weight=0.2, pb_weight=0.5)] += 1
    assert 0.15 < counts["archive"] / n < 0.25
    assert 0.30 < counts["pb"] / n < 0.50
    assert 0.30 < counts["fresh"] / n < 0.50


def test_focus_mix_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RE1_RESET_FOCUS_ROOM", raising=False)
    assert reset_mix_from_env() is None
    monkeypatch.setenv("RE1_RESET_FOCUS_ROOM", "108")
    mix = reset_mix_from_env()
    assert mix is not None
    assert mix.fresh == pytest.approx(0.30)
    assert mix.focus_pb == pytest.approx(0.30)
    assert mix.other_pb == pytest.approx(0.30)
    assert mix.archive == pytest.approx(0.10)
    assert focus_room_from_env() == "108"


def test_sample_reset_mix_renormalizes_missing_archive() -> None:
    rng = random.Random(7)
    weights = ResetMixWeights(0.30, 0.30, 0.30, 0.10)
    counts: Counter[str] = Counter()
    n = 4000
    for _ in range(n):
        counts[
            sample_reset_mix(
                rng,
                weights,
                focus_pb_available=True,
                other_pb_available=True,
                archive_available=False,
            )
        ] += 1
    assert counts["archive"] == 0
    assert 0.24 < counts["fresh"] / n < 0.36
    assert 0.24 < counts["focus_pb"] / n < 0.36
    assert 0.24 < counts["other_pb"] / n < 0.36


def test_sample_reset_mix_distribution_all_available() -> None:
    rng = random.Random(11)
    weights = ResetMixWeights(0.30, 0.30, 0.30, 0.10)
    counts: Counter[str] = Counter()
    n = 8000
    for _ in range(n):
        counts[
            sample_reset_mix(
                rng,
                weights,
                focus_pb_available=True,
                other_pb_available=True,
                archive_available=True,
            )
        ] += 1
    assert 0.24 < counts["fresh"] / n < 0.36
    assert 0.24 < counts["focus_pb"] / n < 0.36
    assert 0.24 < counts["other_pb"] / n < 0.36
    assert 0.06 < counts["archive"] / n < 0.14
