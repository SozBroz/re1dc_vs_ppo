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

from re1_rl.pb_champion import try_replace_champion
from re1_rl.pb_curriculum import (
    MANIFEST_VERSION,
    PbBundle,
    load_pb_manifest,
    sample_reset_bundle,
    sample_typewriter_start,
    typewriter_mix_weights,
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


def test_typewriter_mix_weights_n0_to_n3() -> None:
    assert typewriter_mix_weights(0) == (1.0, 0.0)
    assert typewriter_mix_weights(1) == (0.5, 0.5)
    p_fresh2, p_each2 = typewriter_mix_weights(2)
    assert p_fresh2 == pytest.approx(1.0 / 3.0)
    assert p_each2 == pytest.approx((2.0 / 3.0) / 2.0)
    p_fresh3, p_each3 = typewriter_mix_weights(3)
    assert p_fresh3 == pytest.approx(1.0 / 3.0)
    assert p_each3 == pytest.approx((2.0 / 3.0) / 3.0)
    # Sidecars share the 2/3 mass; fresh pinned at 1/3 for N>=2.
    assert p_fresh3 + 3 * p_each3 == pytest.approx(1.0)


def _seed_typewriter_champion(project_root: Path, room: str, tag: bytes) -> None:
    state_path = project_root / f"{room}.State"
    sidecar_path = project_root / f"{room}.sidecar.json"
    state_path.write_bytes(tag)
    sidecar_path.write_text("{}", encoding="utf-8")
    assert try_replace_champion(
        project_root,
        state_path=state_path,
        sidecar_path=sidecar_path,
        state={
            "room_id": room,
            "hp": 100,
            "inventory_slots": [["beretta", 10], ["ink_ribbon", 1]],
            "inventory": ["beretta", "ink_ribbon"],
        },
        room_id=room,
        visited_rooms=(room,),
    )


def test_sample_typewriter_start_n0_always_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RE1_PB_SHARED_ROOT", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    rng = random.Random(0)
    for _ in range(20):
        assert sample_typewriter_start(empty, rng=rng) is None


def test_sample_typewriter_start_n1_half_half(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RE1_PB_SHARED_ROOT", raising=False)
    _seed_typewriter_champion(tmp_path, "106", b"A")
    rng = random.Random(1)
    n = 4000
    pb_hits = sum(
        1 for _ in range(n) if sample_typewriter_start(tmp_path, rng=rng) is not None
    )
    frac = pb_hits / n
    assert 0.45 <= frac <= 0.55


def test_sample_typewriter_start_n2_and_n3_fresh_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RE1_PB_SHARED_ROOT", raising=False)
    _seed_typewriter_champion(tmp_path, "106", b"A")
    _seed_typewriter_champion(tmp_path, "118", b"B")

    rng = random.Random(7)
    n = 9000
    counts: Counter[str | None] = Counter()
    for _ in range(n):
        picked = sample_typewriter_start(tmp_path, rng=rng)
        if picked is None:
            counts[None] += 1
        else:
            counts[picked["state_path"]] += 1

    fresh_frac = counts[None] / n
    assert 0.30 <= fresh_frac <= 0.36
    pb_frac = 1.0 - fresh_frac
    assert 0.64 <= pb_frac <= 0.70
    # Two sidecars share the PB mass roughly evenly.
    pb_keys = [k for k in counts if k is not None]
    assert len(pb_keys) == 2
    for k in pb_keys:
        assert 0.28 <= counts[k] / n <= 0.38

    _seed_typewriter_champion(tmp_path, "100", b"C")
    rng3 = random.Random(11)
    n3 = 12000
    counts3: Counter[str | None] = Counter()
    for _ in range(n3):
        picked = sample_typewriter_start(tmp_path, rng=rng3)
        counts3[None if picked is None else picked["state_path"]] += 1

    fresh3 = counts3[None] / n3
    assert 0.30 <= fresh3 <= 0.36
    pb_keys3 = [k for k in counts3 if k is not None]
    assert len(pb_keys3) == 3
    for k in pb_keys3:
        # Each sidecar ≈ (2/3)/3 = 2/9 ≈ 0.222
        assert 0.18 <= counts3[k] / n3 <= 0.27
