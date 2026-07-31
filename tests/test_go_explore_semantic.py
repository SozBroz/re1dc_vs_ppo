"""Tests for Go-Explore semantic bucket helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.go_explore_semantic import (
    bucket_pose_count,
    keep_best_rows,
    manifest_index_by_semantic_bucket,
    max_archive_cells,
    pose_cap,
    pose_evict_enabled,
    semantic_admission_allowed,
    semantic_bucket_key,
    semantic_bucket_key_from_cell_key,
    weakest_incumbent,
)
from re1_rl.milestone_digest import cell_key_v2


def _row(
    *,
    record_id: str,
    room: str,
    tx: int,
    tz: int,
    digest: str,
    quality: list[int],
    visit_count: int = 0,
    captured_at_iso: str = "",
) -> dict:
    key = f"v2|r={room}|x={tx}|z={tz}|m={digest}"
    return {
        "record_id": record_id,
        "cell_key": key,
        "room_id": room,
        "quality": quality,
        "visit_count": visit_count,
        "captured_at_iso": captured_at_iso,
        "tile_bin": [tx, tz],
    }


def test_semantic_bucket_key_helpers() -> None:
    assert semantic_bucket_key("105", "gallery:idle") == ("105", "gallery:idle")
    key = cell_key_v2("105", 4096 * 3, 4096 * 2, "gallery:idle")
    assert semantic_bucket_key_from_cell_key(key) == ("105", "gallery:idle")


def test_manifest_index_groups_by_bucket() -> None:
    rows = [
        _row(record_id="a", room="105", tx=1, tz=0, digest="gallery:idle", quality=[1, 0, 0, 0, 1]),
        _row(record_id="b", room="105", tx=2, tz=0, digest="gallery:idle", quality=[2, 0, 0, 0, 1]),
        _row(record_id="c", room="105", tx=1, tz=0, digest="got:emblem", quality=[3, 0, 0, 0, 1]),
    ]
    index = manifest_index_by_semantic_bucket(rows)
    assert bucket_pose_count(index, ("105", "gallery:idle")) == 2
    assert bucket_pose_count(index, ("105", "got:emblem")) == 1

    from_manifest = manifest_index_by_semantic_bucket({"archive_version": 1, "cells": rows})
    assert bucket_pose_count(from_manifest, ("105", "gallery:idle")) == 2

    by_key = {r["cell_key"]: r for r in rows}
    from_index = manifest_index_by_semantic_bucket(by_key)
    assert bucket_pose_count(from_index, ("105", "gallery:idle")) == 2


def test_weakest_incumbent_prefers_low_quality_then_central_tile() -> None:
    rows = [
        _row(record_id="strong", room="105", tx=0, tz=0, digest="d", quality=[90, 0, 0, 0, 1]),
        _row(record_id="weak_edge", room="105", tx=10, tz=0, digest="d", quality=[10, 0, 0, 0, 1]),
        _row(record_id="weak_center", room="105", tx=5, tz=0, digest="d", quality=[10, 0, 0, 0, 1]),
    ]
    # Centroid x ≈ 5; among equal quality, central tile evicts first.
    weak = weakest_incumbent(rows)
    assert weak is not None
    assert weak["record_id"] == "weak_center"


def test_keep_best_rows() -> None:
    rows = [
        _row(record_id=f"r{i}", room="105", tx=i, tz=0, digest="d", quality=[i, 0, 0, 0, 1])
        for i in range(8)
    ]
    kept = keep_best_rows(rows, 6)
    assert len(kept) == 6
    ids = {r["record_id"] for r in kept}
    assert "r0" not in ids and "r1" not in ids


def test_env_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RE1_GO_MAX_POSES_PER_BUCKET", raising=False)
    monkeypatch.delenv("RE1_GO_MAX_ARCHIVE_CELLS", raising=False)
    monkeypatch.delenv("RE1_GO_POSE_EVICT", raising=False)
    assert pose_cap() == 6
    assert max_archive_cells() == 8000
    assert pose_evict_enabled() is True

    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "4")
    monkeypatch.setenv("RE1_GO_MAX_ARCHIVE_CELLS", "100")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "0")
    assert pose_cap() == 4
    assert max_archive_cells() == 100
    assert pose_evict_enabled() is False


def test_semantic_admission_under_cap_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "6")
    rows = [
        _row(record_id=f"r{i}", room="105", tx=i, tz=0, digest="d", quality=[50, 0, 0, 0, 1])
        for i in range(3)
    ]
    manifest_index = {r["cell_key"]: r for r in rows}
    sem = manifest_index_by_semantic_bucket(manifest_index)
    new_key = cell_key_v2("105", 4096 * 9, 0, "d")
    assert semantic_admission_allowed(
        "105", "d", new_key, (40, 0, 0, 0, 1),
        manifest_index=manifest_index,
        semantic_index=sem,
    )


def test_semantic_admission_at_cap_rejects_weaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "3")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "1")
    rows = [
        _row(record_id=f"r{i}", room="105", tx=i, tz=0, digest="d", quality=[50 + i, 0, 0, 0, 1])
        for i in range(3)
    ]
    manifest_index = {r["cell_key"]: r for r in rows}
    sem = manifest_index_by_semantic_bucket(manifest_index)
    new_key = cell_key_v2("105", 4096 * 9, 0, "d")
    assert not semantic_admission_allowed(
        "105", "d", new_key, (10, 0, 0, 0, 1),
        manifest_index=manifest_index,
        semantic_index=sem,
    )


def test_semantic_admission_at_cap_allows_stronger_when_evict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "3")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "1")
    rows = [
        _row(record_id=f"r{i}", room="105", tx=i, tz=0, digest="d", quality=[50 + i, 0, 0, 0, 1])
        for i in range(3)
    ]
    manifest_index = {r["cell_key"]: r for r in rows}
    sem = manifest_index_by_semantic_bucket(manifest_index)
    new_key = cell_key_v2("105", 4096 * 9, 0, "d")
    assert semantic_admission_allowed(
        "105", "d", new_key, (90, 0, 0, 0, 1),
        manifest_index=manifest_index,
        semantic_index=sem,
    )


def test_semantic_admission_new_digest_always_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "3")
    rows = [
        _row(record_id=f"r{i}", room="105", tx=i, tz=0, digest="old", quality=[99, 0, 0, 0, 1])
        for i in range(3)
    ]
    manifest_index = {r["cell_key"]: r for r in rows}
    sem = manifest_index_by_semantic_bucket(manifest_index)
    new_key = cell_key_v2("105", 0, 0, "new_digest")
    assert semantic_admission_allowed(
        "105", "new_digest", new_key, (1, 0, 0, 0, 1),
        manifest_index=manifest_index,
        semantic_index=sem,
    )


def test_semantic_admission_same_key_replace_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE1_GO_REPLACE_MIN_HP_DELTA", "5")
    key = cell_key_v2("105", 0, 0, "d")
    manifest_index = {
        key: _row(record_id="old", room="105", tx=0, tz=0, digest="d", quality=[40, 0, 0, 0, 1]),
    }
    assert not semantic_admission_allowed(
        "105", "d", key, (42, 0, 0, 0, 1),
        manifest_index=manifest_index,
    )
    assert semantic_admission_allowed(
        "105", "d", key, (50, 0, 0, 0, 1),
        manifest_index=manifest_index,
    )
