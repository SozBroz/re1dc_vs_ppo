"""Planner-loyal cell sync: prefix default, version-0 manifest, ingest reasons."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.yawn_rails_sync import (
    YawnRailsCellStore,
    build_capture_proposal,
    cell_dir_name,
    cell_dir_prefix,
    yawn_rails_rel_path,
)


def _cell(tmp: Path, idx: int, *, prefix: str | None = None) -> dict:
    name = f"{prefix}{idx:02d}" if prefix else cell_dir_name(idx)
    cell = tmp / "cells" / name
    cell.mkdir(parents=True, exist_ok=True)
    state = cell / "cell.State"
    side = cell / "cell.sidecar.json"
    state.write_bytes(b"STATE_%02d" % idx)
    side.write_text(json.dumps({"checkpoint_index": idx}) + "\n", encoding="utf-8")
    return build_capture_proposal(
        route_id="planner_loyal_v1",
        checkpoint_index=idx,
        checkpoint_id=f"step{idx:02d}",
        room_id="10F",
        quality=(96, 75, 100, 7, 1, 0, -30, -10),
        state_path=state,
        sidecar_path=side,
        worker_id="wh2",
    )


def test_planner_loyal_defaults_prefix_and_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RE1_YAWN_CELL_PREFIX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RAILS_ROOT", raising=False)
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    assert cell_dir_prefix() == "pl"
    assert cell_dir_name(11) == "pl11"
    assert yawn_rails_rel_path() == "states/planner_loyal"


def test_manifest_version_zero_returns_snapshot(tmp_path: Path) -> None:
    cells = []
    for idx in range(3):
        row = _cell(tmp_path, idx, prefix="cp")
        cells.append(
            {
                "checkpoint_index": idx,
                "checkpoint_id": f"seed{idx}",
                "room_id": "106",
                "quality": [96, 45, 100, 4, 1, 0, -30, -1],
                "state_sha256": row["state_sha256"],
                "sidecar_sha256": row["sidecar_sha256"],
            }
        )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "archive_version": 0,
                "route_id": "planner_loyal_v1",
                "cells": cells,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = YawnRailsCellStore(tmp_path)
    assert store.archive_version == 0
    man = store.build_manifest(since_version=0)
    assert len(man["cells"]) == 3
    assert man["cell_count"] == 3


def test_ingest_reports_missing_bundle(tmp_path: Path) -> None:
    store = YawnRailsCellStore(tmp_path)
    accepted = store.ingest_proposals(
        [
            {
                "route_id": "planner_loyal_v1",
                "checkpoint_index": 11,
                "checkpoint_id": "music",
                "room_id": "10F",
                "quality": [96, 75, 100, 7, 1, 0, -30, -10],
            }
        ]
    )
    assert accepted == []
    assert store.last_rejects == ["idx=11: missing_bundle"]


def test_ingest_new_planner_cell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    monkeypatch.delenv("RE1_YAWN_CELL_PREFIX", raising=False)
    prop = _cell(tmp_path, 11)
    store = YawnRailsCellStore(tmp_path)
    accepted = store.ingest_proposals([prop])
    assert accepted == ["pl11"]
    assert (tmp_path / "cells" / "pl11" / "cell.State").is_file()
