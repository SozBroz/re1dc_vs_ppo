"""Go-Explore archive v2: path filter, frontier, quality, lock, migration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.go_explore_archive import (
    ARCHIVE_VERSION,
    ArchiveCell,
    GoExploreArchive,
    acquire_archive_lock,
    quality_beats,
    release_archive_lock,
    tile_bin,
)
from re1_rl.milestone_digest import YAWN_PATH_ROOMS, cell_key_v2


def test_archive_version_is_2() -> None:
    assert ARCHIVE_VERSION == 2


def test_tile_bin_default_4096() -> None:
    assert tile_bin(0, 0) == (0, 0)
    assert tile_bin(4095, 4096) == (0, 1)
    assert tile_bin(9000, -100) == (2, -1)


def test_quality_beats_lexicographic() -> None:
    assert quality_beats((8, 12, 2, 3, 0), None)
    assert quality_beats((9, 0, 0, 0, 0), (8, 99, 99, 99, 99))
    assert not quality_beats((8, 12, 2, 3, 0), (8, 12, 2, 3, 0))
    assert quality_beats((8, 13, 0, 0, 0), (8, 12, 9, 9, 9))
    # Lowest priority: less ammo left in the box (higher -box_ammo) wins.
    assert quality_beats((8, 30, 0, 0, 1, 0, 0), (8, 30, 0, 0, 1, 0, -30))
    assert not quality_beats((8, 30, 0, 0, 1, 0, -30), (8, 30, 0, 0, 1, 0, 0))


def test_upsert_and_frontier_yawn_filter(tmp_path: Path) -> None:
    path = tmp_path / "archive.json"
    arch = GoExploreArchive(path, max_cells_per_room=40)
    digest_a = "gallery:idle"
    digest_b = "got:emblem"

    arch.upsert(
        room_id="20E",
        x=100,
        z=100,
        digest=digest_a,
        quality=(5, 1, 0, 1, 1),
        bundle_path="cells/a/cell.State",
    )
    arch.upsert(
        room_id="20E",
        x=5000,
        z=100,
        digest=digest_b,
        quality=(8, 2, 0, 1, 1),
    )
    # Off-path room — still in the frontier pool when no filter is passed.
    arch.upsert(
        room_id="300",
        x=0,
        z=0,
        digest=digest_a,
        quality=(99, 99, 99, 99, 99),
    )
    for _ in range(5):
        arch.upsert(
            room_id="300",
            x=0,
            z=0,
            digest=digest_a,
            quality=(99, 99, 99, 99, 99),
        )
    # Bump visits on the stronger 20E cell so frontier prefers the under-visited one.
    for _ in range(3):
        arch.upsert(
            room_id="20E",
            x=5000,
            z=100,
            digest=digest_b,
            quality=(8, 2, 0, 1, 1),
        )

    picked = arch.select_frontier(k=1, rng=__import__("random").Random(0))
    assert len(picked) == 1
    assert picked[0].room_id == "20E"
    assert picked[0].visit_count == 1
    assert picked[0].milestone_digest == digest_a

    # Explicit filter still works.
    off = arch.select_frontier(room_ids={"300"}, k=1)
    assert len(off) == 1
    assert off[0].room_id == "300"


def test_select_frontier_defaults_to_yawn_rooms(tmp_path: Path) -> None:
    arch = GoExploreArchive(tmp_path / "a.json")
    arch.upsert(room_id="105", x=0, z=0, digest="gallery:idle", quality=(1, 0, 0, 0, 0))
    arch.upsert(room_id="999", x=0, z=0, digest="gallery:idle", quality=(1, 0, 0, 0, 0))
    picked = arch.select_frontier(room_ids=YAWN_PATH_ROOMS, k=5)
    assert all(c.room_id in YAWN_PATH_ROOMS for c in picked)
    assert {c.room_id for c in picked} == {"105"}


def test_max_cells_per_room_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE1_GO_MAX_CELLS_PER_ROOM", "2")
    arch = GoExploreArchive(tmp_path / "a.json", max_cells_per_room=2)
    digest_a = "gallery:idle"
    digest_b = "got:emblem"
    assert arch.upsert(room_id="105", x=0, z=0, digest=digest_a, quality=(1, 0, 0, 0, 0))
    # Same digest at a new tile replaces the bucket champion (one cell per digest).
    replaced = arch.upsert(room_id="105", x=5000, z=0, digest=digest_a, quality=(5, 0, 0, 0, 0))
    assert replaced is not None
    assert len(arch.cells) == 1
    assert arch.upsert(room_id="105", x=9000, z=0, digest=digest_b, quality=(1, 0, 0, 0, 0))
    assert len(arch.cells) == 2
    rejected = arch.upsert(
        room_id="105", x=10000, z=0, digest="carry:map", quality=(9, 9, 9, 9, 9)
    )
    assert rejected is None
    # Existing key still updates.
    again = arch.upsert(room_id="105", x=0, z=0, digest=digest_a, quality=(2, 0, 0, 0, 0))
    assert again is not None
    assert again.visit_count == 1
    assert again.quality[0] == 2


def test_save_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "archive.json"
    arch = GoExploreArchive(path)
    digest = "carry:emblem|gallery:idle"
    arch.upsert(
        room_id="10F",
        x=100,
        z=200,
        digest=digest,
        quality=(7, 4, 1, 2, 1),
        bundle_path="cells/x/cell.State",
        meta={"worker": "test"},
    )
    arch.save()

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2
    key = cell_key_v2("10F", 100, 200, digest)
    assert key in raw["cells"]

    loaded = GoExploreArchive(path)
    loaded.load()
    cell = loaded.cells[key]
    assert isinstance(cell, ArchiveCell)
    assert cell.room_id == "10F"
    assert cell.milestone_digest == digest
    assert cell.quality == (7, 4, 1, 2, 1, 0, 0)
    assert cell.bundle_path == "cells/x/cell.State"
    assert cell.meta["worker"] == "test"
    assert cell.record_id


def test_migrate_v1_minimally(tmp_path: Path) -> None:
    path = tmp_path / "archive.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cells": {
                    "105:3,1": {
                        "room_id": "105",
                        "tile_bin": [3, 1],
                        "score": 1.5,
                        "visit_count": 2,
                        "state_path": "old.State",
                        "meta": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    arch = GoExploreArchive(path, migrate_v1=True)
    arch.load()
    assert len(arch.cells) == 1
    cell = next(iter(arch.cells.values()))
    assert cell.room_id == "105"
    assert cell.tile_bin == (3, 1)
    assert cell.milestone_digest == ""
    assert cell.visit_count == 2
    assert cell.bundle_path == "old.State"
    assert cell.cell_key.startswith("v2|")
    assert cell.meta.get("legacy_score") == 1.5

    arch.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2


def test_reject_v1_when_migrate_disabled(tmp_path: Path) -> None:
    path = tmp_path / "archive.json"
    path.write_text(json.dumps({"version": 1, "cells": {}}), encoding="utf-8")
    arch = GoExploreArchive(path, migrate_v1=False)
    with pytest.raises(ValueError, match="unsupported archive version 1"):
        arch.load()


def test_archive_lock_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "archive.json"
    assert acquire_archive_lock(path, holder="a")
    assert not acquire_archive_lock(path, holder="b")
    release_archive_lock(path)
    assert acquire_archive_lock(path, holder="b")
    release_archive_lock(path)
