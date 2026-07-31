"""Tests for Go-Explore manifest analytics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.go_explore_analytics import (
    analyze_manifest,
    format_report_text,
    manifest_row_to_cell,
    report_to_dict,
)


def _row(
    *,
    record_id: str,
    room: str,
    tx: int,
    tz: int,
    digest: str,
    quality: list[int] | None = None,
    nbytes: int = 1_500_000,
) -> dict:
    return {
        "record_id": record_id,
        "cell_key": f"v2|r={room}|x={tx}|z={tz}|m={digest}",
        "room_id": room,
        "quality": quality or [96, 15, 1, 2, 1],
        "bytes": nbytes,
    }


def test_manifest_row_to_cell_parses_v2_key() -> None:
    cell = manifest_row_to_cell(_row(record_id="a", room="105", tx=1, tz=2, digest="gallery:idle"))
    assert cell is not None
    assert cell.room_id == "105"
    assert cell.tile == (1, 2)
    assert cell.milestone_digest == "gallery:idle"


def test_analyze_flags_pose_redundant_bucket() -> None:
    manifest = {
        "archive_version": 3,
        "cells": [
            _row(record_id=f"r{i}", room="105", tx=i, tz=0, digest="gallery:idle")
            for i in range(10)
        ],
    }
    report = analyze_manifest(manifest, pose_warn_threshold=8)
    assert report.cells_real == 10
    assert len(report.buckets) == 1
    assert report.buckets[0].pose_count == 10
    assert len(report.buckets_over_pose_threshold) == 1
    assert "105" in report.rooms_seen


def test_analyze_excludes_probe_record_ids() -> None:
    manifest = {
        "archive_version": 1,
        "cells": [
            _row(record_id="probe_live_001", room="20E", tx=0, tz=0, digest="gallery:idle"),
            _row(record_id="real1", room="106", tx=0, tz=0, digest="event:kenneth_done"),
        ],
    }
    report = analyze_manifest(manifest)
    assert report.cells_total == 2
    assert report.cells_real == 1
    assert report.rooms_seen == ["106"]


def test_analyze_multi_digest_same_tile() -> None:
    manifest = {
        "archive_version": 1,
        "cells": [
            _row(record_id="a", room="105", tx=1, tz=1, digest="gallery:idle"),
            _row(record_id="b", room="105", tx=1, tz=1, digest="carry:emblem|got:emblem|gallery:idle"),
        ],
    }
    report = analyze_manifest(manifest)
    assert len(report.multi_digest_tiles) == 1
    assert report.multi_digest_tiles[0].room_id == "105"
    assert len(report.multi_digest_tiles[0].digests) == 2


def test_report_to_dict_and_text_smoke() -> None:
    manifest = {
        "archive_version": 2,
        "cells": [
            _row(record_id="c1", room="105", tx=0, tz=0, digest="gallery:idle"),
            _row(record_id="c2", room="106", tx=0, tz=0, digest="event:kenneth_done"),
        ],
    }
    report = analyze_manifest(manifest)
    d = report_to_dict(report)
    assert d["cells_real"] == 2
    assert "105" in d["by_room"]
    text = format_report_text(report)
    assert "Go-Explore manifest analytics" in text
    assert "105" in text


def test_analyze_from_snapshot_file(tmp_path: Path) -> None:
    snap = tmp_path / "manifest.json"
    snap.write_text(
        json.dumps(
            {
                "archive_version": 21,
                "cells": [_row(record_id="x", room="203", tx=2, tz=4, digest="event:kenneth_done")],
            }
        ),
        encoding="utf-8",
    )
    data = json.loads(snap.read_text(encoding="utf-8"))
    report = analyze_manifest(data, source=str(snap))
    assert report.cells_real == 1
    assert report.yawn_rooms_seen == ["203"]
