"""Progress-gated Go-Explore capture reasons."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.go_explore_capture import quality_replace_significant
from re1_rl.go_explore_progress import (
    bucket_new_reason,
    coverage_reason,
    detect_go_explore_progress_events,
    quality_improve_reason,
)
from re1_rl.reset_curriculum import sample_reset_source


def test_detect_new_room_and_weapon() -> None:
    prev = {"room_id": "100"}
    state = {"room_id": "101", "new_items": ["beretta"]}
    bd = {"new_room": 1.0, "new_weapon": 4.0}
    events = detect_go_explore_progress_events(prev, state, bd)
    assert "room:101" in events
    assert "weapon:beretta" in events


def test_detect_dining_statue_and_cutscene() -> None:
    prev = {"room_id": "105"}
    state = {"room_id": "105", "cutscene_key": "kenneth"}
    bd = {"dining_statue": 4.0, "cutscene": 1.0}
    events = detect_go_explore_progress_events(prev, state, bd)
    assert "dining_statue" in events
    assert "cutscene:kenneth" in events


def test_no_events_on_idle_step() -> None:
    prev = {"room_id": "100"}
    state = {"room_id": "100"}
    events = detect_go_explore_progress_events(prev, state, {"alive": 0.001})
    assert events == []


def test_coverage_and_quality_reason_helpers() -> None:
    assert coverage_reason("108") == "coverage:108"
    assert bucket_new_reason("108", "gallery:idle") == "bucket_new:108:gallery:idle"
    assert quality_improve_reason("v2|x").startswith("quality_improve:")


def test_quality_replace_significant_resource_gain() -> None:
    poor = (50, 0, 0, 0, 1)
    rich = (50, 20, 2, 1, 1)
    assert quality_replace_significant(rich, poor)
    assert not quality_replace_significant(poor, rich)


def test_reset_mix_30_30_40() -> None:
    """archive=0.40 and pb_weight=0.5 → ~40% cells / 30% PB / 30% fresh."""
    import random
    from collections import Counter

    rng = random.Random(0)
    counts: Counter[str] = Counter()
    n = 8000
    for _ in range(n):
        counts[sample_reset_source(rng, archive_weight=0.40, pb_weight=0.5)] += 1
    assert 0.35 < counts["archive"] / n < 0.45
    assert 0.25 < counts["pb"] / n < 0.35
    assert 0.25 < counts["fresh"] / n < 0.35
