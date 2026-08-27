"""Planner-loyal cell bootstrap + slot mapping."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.go_explore_merge import CELL_SIDECAR_NAME, CELL_STATE_NAME
from re1_rl.planner_loyal_cells import (
    TRAINING_START_INDEX,
    assemble_planner_loyal_quality,
    bootstrap_from_crystals,
    cell_dir_name,
    cell_has_remaining_planner_step,
    iter_training_start_cells,
    lift_planner_loyal_quality,
    planner_loyal_quality_beats,
    planner_loyal_root,
    seek_index_after_cell,
    slot_index_for_completed_step,
    training_start_paths,
)


def test_slot_mapping() -> None:
    assert slot_index_for_completed_step(0) == TRAINING_START_INDEX + 1
    assert slot_index_for_completed_step(1) == TRAINING_START_INDEX + 2
    assert cell_dir_name(5) == "pl05"


def test_bootstrap_crystals_tip_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    crystals = root / "backups" / "Crystals_in_time" / "cp05" / "cell.State"
    if not crystals.is_file():
        return  # skip if archive absent in CI
    result = bootstrap_from_crystals(root, force=False)
    tip = training_start_paths(root)
    assert tip["state"].is_file()
    assert tip["sidecar"].is_file()
    assert result["training_start_index"] == TRAINING_START_INDEX
    assert (planner_loyal_root(root) / "manifest.json").is_file()
    # Thin: no leg_replay on tip after bootstrap refresh strip.
    from re1_rl.planner_loyal_cells import _strip_fat_artifacts

    _strip_fat_artifacts(tip["cell_dir"])
    assert not (tip["cell_dir"] / "leg_replay.json").is_file()


def _write_cell(root: Path, idx: int, *, meta: dict | None = None) -> None:
    slot = root / "states" / "planner_loyal" / "cells" / cell_dir_name(idx)
    slot.mkdir(parents=True, exist_ok=True)
    (slot / CELL_STATE_NAME).write_bytes(b"STATE")
    (slot / CELL_SIDECAR_NAME).write_text("{}", encoding="utf-8")
    payload = {"checkpoint_index": idx, "checkpoint_id": f"cell_{idx}"}
    if meta:
        payload.update(meta)
    (slot / "meta.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_training_starts_are_pl05_and_every_later_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_RANGE", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_SET", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_FILE", raising=False)
    monkeypatch.delenv("RE1_PLANNER_CHUNK", raising=False)
    _write_cell(tmp_path, 4, meta={"training_start": False})
    _write_cell(tmp_path, 5, meta={"training_start": True})
    _write_cell(tmp_path, 10, meta={"training_start": True})
    # Synced cells often drop the flag; slot index still qualifies.
    _write_cell(tmp_path, 11, meta={})
    _write_cell(tmp_path, 18, meta={"chunk_final": True, "training_start": False})

    starts = iter_training_start_cells(tmp_path)
    # Live chemical tail still has steps after pl18, so it stays in the pool.
    assert [int(row["checkpoint_index"]) for row in starts] == [5, 10, 11, 18]


def test_exhausted_last_step_cell_is_not_a_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_RANGE", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_SET", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_FILE", raising=False)
    chunk = tmp_path / "chunk.json"
    chunk.write_text(
        json.dumps(
            {
                "chunk_id": "short",
                "steps": [
                    {"n": 1, "op": "traverse", "edge_id": "106->105"},
                    {"n": 2, "op": "traverse", "edge_id": "105->104"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RE1_PLANNER_CHUNK", str(chunk))
    _write_cell(tmp_path, 5, meta={"planner_step_index": None})
    _write_cell(tmp_path, 6, meta={"planner_step_index": 0})
    _write_cell(tmp_path, 7, meta={"planner_step_index": 1, "chunk_final": True})
    starts = iter_training_start_cells(tmp_path)
    assert [int(row["checkpoint_index"]) for row in starts] == [5, 6]
    assert seek_index_after_cell({"planner_step_index": 1, "checkpoint_index": 7}) == 2
    assert cell_has_remaining_planner_step(
        {"planner_step_index": 1, "checkpoint_index": 7}, 2
    ) is False


def _write_pin(root: Path, text: str) -> Path:
    path = root / "data" / "planner_loyal_reset_pin.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _seed_starts(tmp_path: Path) -> None:
    for idx in (5, 10, 11, 13):
        _write_cell(tmp_path, idx)


def test_reset_pin_file_index_and_hot_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_RANGE", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_SET", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_FILE", raising=False)
    _seed_starts(tmp_path)
    pin = _write_pin(
        tmp_path,
        "RE1_PLANNER_RESET_PIN_INDEX=11\n"
        "RE1_PLANNER_RESET_PIN_RANGE=\n"
        "RE1_PLANNER_RESET_PIN_SET=\n",
    )
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [
        11
    ]
    pin.write_text(
        "RE1_PLANNER_RESET_PIN_INDEX=\n"
        "RE1_PLANNER_RESET_PIN_RANGE=10-13\n"
        "RE1_PLANNER_RESET_PIN_SET=\n",
        encoding="utf-8",
    )
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [
        10,
        11,
        13,
    ]


def test_reset_pin_set_and_unminted_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_RANGE", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_SET", raising=False)
    monkeypatch.delenv("RE1_PLANNER_RESET_PIN_FILE", raising=False)
    _seed_starts(tmp_path)
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_SET=5,11\n")
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [
        5,
        11,
    ]
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_INDEX=99\n")
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [
        5,
        10,
        11,
        13,
    ]


def test_planner_loyal_quality_ranks_kills_after_hp() -> None:
    legacy = (96, 60, 66, 11, 1, 0, -30, -30)
    assert lift_planner_loyal_quality(legacy) == (96, 0, 60, 66, 11, 1, 0, -30, -30)
    minted = assemble_planner_loyal_quality(legacy, 4)
    assert minted == (96, 4, 60, 66, 11, 1, 0, -30, -30)
    # Same HP: more path kills win even with less ammo.
    assert planner_loyal_quality_beats((96, 3, 20, 0, 0, 1, 0, 0, -10), minted) is False
    assert planner_loyal_quality_beats(minted, (96, 3, 99, 99, 99, 1, 0, 0, -1)) is True
    # Lower HP loses even with more kills.
    assert planner_loyal_quality_beats((80, 20, 99, 99, 99, 1, 0, 0, -1), minted) is False
    # Legacy 8-tuple (implied 0 kills) loses to a same-HP 4-kill recapture.
    assert planner_loyal_quality_beats(minted, legacy) is True
