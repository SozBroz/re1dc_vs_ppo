"""Unit tests for PB manifest load + reset mix sampling."""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.pb_curriculum import (
    MANIFEST_VERSION,
    PbBundle,
    load_pb_manifest,
    sample_reset_bundle,
)


def _write_manifest(path: Path, bundles: list[dict]) -> None:
    path.write_text(
        json.dumps({"version": MANIFEST_VERSION, "bundles": bundles}),
        encoding="utf-8",
    )


def test_load_pb_manifest_missing_returns_empty(tmp_path: Path) -> None:
    assert load_pb_manifest(tmp_path / "missing.json") == []


def test_load_pb_manifest_roundtrip(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "milestone_id": "shield_key_held",
                "state_path": "states/pb/a.State",
                "sidecar_path": "states/pb/a.sidecar.json",
                "meta": {"room_id": "105", "score": 3.0},
            }
        ],
    )
    bundles = load_pb_manifest(manifest)
    assert len(bundles) == 1
    b = bundles[0]
    assert b.milestone_id == "shield_key_held"
    assert b.state_path == "states/pb/a.State"
    assert b.sidecar_path == "states/pb/a.sidecar.json"
    assert b.meta["room_id"] == "105"


def test_load_pb_manifest_bad_version(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"version": 99, "bundles": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported PB manifest version"):
        load_pb_manifest(path)


def test_sample_always_fresh_when_weight_one() -> None:
    bundles = [
        PbBundle("a.State", "a.json", "m0"),
        PbBundle("b.State", "b.json", "m1"),
    ]
    rng = random.Random(0)
    for _ in range(50):
        assert sample_reset_bundle(bundles, fresh_weight=1.0, rng=rng) is None


def test_sample_always_pb_when_weight_zero_one_bundle() -> None:
    only = PbBundle("only.State", "only.json", "lockpick_held")
    rng = random.Random(0)
    for _ in range(20):
        assert sample_reset_bundle([only], fresh_weight=0.0, rng=rng) is only


def test_sample_empty_bundles_always_fresh() -> None:
    rng = random.Random(0)
    assert sample_reset_bundle([], fresh_weight=0.0, rng=rng) is None
    assert sample_reset_bundle([], fresh_weight=0.5, rng=rng) is None


def test_sample_many_bundles_uniform_among_pbs() -> None:
    a = PbBundle("a.State", "a.json", "m0")
    b = PbBundle("b.State", "b.json", "m1")
    c = PbBundle("c.State", "c.json", "m2")
    bundles = [a, b, c]
    rng = random.Random(123)
    n = 30_000
    counts: Counter[str | None] = Counter()
    for _ in range(n):
        picked = sample_reset_bundle(bundles, fresh_weight=0.25, rng=rng)
        key = None if picked is None else picked.milestone_id
        counts[key] += 1

    fresh_frac = counts[None] / n
    assert 0.23 <= fresh_frac <= 0.27

    pb_total = n - counts[None]
    per_pb = pb_total / 3
    for mid in ("m0", "m1", "m2"):
        frac = counts[mid] / n
        assert 0.22 <= frac <= 0.28
        assert abs(counts[mid] - per_pb) / per_pb < 0.08


def test_sample_invalid_fresh_weight() -> None:
    with pytest.raises(ValueError, match="fresh_weight"):
        sample_reset_bundle([], fresh_weight=1.5)
