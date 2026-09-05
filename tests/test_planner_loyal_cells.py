"""Planner-loyal cell bootstrap + slot mapping."""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import pytest

from re1_rl.go_explore_archive import LEG_FRAMES_SENTINEL
from re1_rl.go_explore_merge import CELL_SIDECAR_NAME, CELL_STATE_NAME
from re1_rl.planner_loyal import chunk_path_for_id
from re1_rl.planner_loyal_cells import (
    FRESH_START_INDEX,
    SEED_SLOT_OFFSET,
    TRAINING_START_INDEX,
    bootstrap_from_crystals,
    cell_dir_name,
    cell_has_remaining_planner_step,
    close_planner_loyal_stretch,
    iter_training_start_cells,
    lift_planner_loyal_quality,
    planner_loyal_kill_audit,
    planner_loyal_quality_beats,
    planner_loyal_root,
    sample_training_start_cell,
    seek_index_after_cell,
    slot_index_for_completed_step,
    training_start_paths,
)
from re1_rl.progress import ProgressTracker

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_slot_numbering_pl00_fresh_pl06_tip_pl07_live() -> None:
    assert FRESH_START_INDEX == 0
    assert TRAINING_START_INDEX == 6
    assert SEED_SLOT_OFFSET == 1
    assert cell_dir_name(FRESH_START_INDEX) == "pl00"
    assert cell_dir_name(TRAINING_START_INDEX) == "pl06"
    # Live chunk (cp05_shield_key) completed step 0 = 106->105 mints pl07.
    assert slot_index_for_completed_step(0) == 7
    assert slot_index_for_completed_step(0) == TRAINING_START_INDEX + 1
    assert slot_index_for_completed_step(1) == TRAINING_START_INDEX + 2
    steps = [
        {"n": 1, "op": "traverse", "edge_id": "204->20D"},
        {"n": 2, "op": "trigger_cutscene", "capture": False},
        {"n": 3, "op": "traverse", "edge_id": "204->207"},
    ]
    assert slot_index_for_completed_step(0, steps) == TRAINING_START_INDEX + 1
    # capture:false does not consume a slot — next capturing hop reuses it.
    assert slot_index_for_completed_step(2, steps) == TRAINING_START_INDEX + 2
    opening = [
        {"op": "acquire", "pickup_id": "105:emblem:1", "slot_index": 1},
        {"op": "traverse", "edge_id": "105->104", "slot_index": 2},
    ]
    assert slot_index_for_completed_step(0, opening) == 1
    assert slot_index_for_completed_step(1, opening) == 2


def test_opening_chunk_slots_are_pl01_to_pl06() -> None:
    path = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    chunk = json.loads(path.read_text(encoding="utf-8"))
    steps = chunk["steps"]
    assert [s["slot_index"] for s in steps] == [1, 2, 3, 4, 5, 6]
    assert steps[0]["pickup_id"] == "105:emblem:1"
    assert steps[-1]["edge_id"] == "203->106"
    assert steps[-1]["slot_index"] == TRAINING_START_INDEX
    for i in range(len(steps)):
        assert slot_index_for_completed_step(i, steps) == i + 1


def test_fresh_start_seeks_to_first_step() -> None:
    row = {"checkpoint_index": 0, "planner_step_index": -1, "training_start": True}
    assert seek_index_after_cell(row) == 0
    assert seek_index_after_cell({"checkpoint_index": TRAINING_START_INDEX}) == 0
    assert seek_index_after_cell({"checkpoint_index": 7, "planner_step_index": 0}) == 1
    assert seek_index_after_cell({"checkpoint_index": 8}) == 2


def test_seek_ignores_step_index_from_another_chunk() -> None:
    # pl06 minted by the opening chunk (its final step) is the live tip: seek 0.
    tip = {
        "checkpoint_index": TRAINING_START_INDEX,
        "planner_step_index": 5,
        "chunk_id": "opening_to_lockpick",
    }
    assert seek_index_after_cell(tip, "opening_to_lockpick") == 6
    assert seek_index_after_cell(tip, "cp05_shield_key") == 0
    live = {"checkpoint_index": 9, "planner_step_index": 2, "chunk_id": "cp05_shield_key"}
    assert seek_index_after_cell(live, "cp05_shield_key") == 3
    assert seek_index_after_cell(live, None) == 3


def test_bootstrap_crystals_shifts_cp_to_pl_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crystals = PROJECT_ROOT / "backups" / "Crystals_in_time" / "cp05" / "cell.State"
    if not crystals.is_file():
        pytest.skip("Crystals archive absent")
    monkeypatch.setenv(
        "RE1_PLANNER_LOYAL_CELLS_ROOT", str(tmp_path / "states" / "planner_loyal")
    )
    result = bootstrap_from_crystals(
        tmp_path, crystals_root=PROJECT_ROOT / "backups" / "Crystals_in_time"
    )
    assert result["training_start_index"] == TRAINING_START_INDEX
    assert [row["checkpoint_index"] for row in result["copied"]] == [1, 2, 3, 4, 5, 6]
    root = planner_loyal_root(tmp_path)
    assert not (root / "cells" / "pl00").exists()  # fresh start is installed by hand
    tip = training_start_paths(tmp_path)
    assert tip["cell_dir"].name == "pl06"
    assert tip["state"].is_file() and tip["sidecar"].is_file()
    meta = json.loads(tip["meta"].read_text(encoding="utf-8"))
    assert meta["checkpoint_index"] == 6
    assert meta["source"]["checkpoint"] == "cp05"
    assert meta["training_start"] is True
    assert json.loads((root / "cells" / "pl01" / "meta.json").read_text())["training_start"] is False
    assert (root / "manifest.json").is_file()
    assert not (tip["cell_dir"] / "leg_replay.json").is_file()
    # Existing slots are never rewritten without --force.
    (tip["meta"]).write_text('{"checkpoint_index": 6, "marker": 1}\n', encoding="utf-8")
    bootstrap_from_crystals(
        tmp_path, crystals_root=PROJECT_ROOT / "backups" / "Crystals_in_time"
    )
    assert json.loads(tip["meta"].read_text(encoding="utf-8")).get("marker") == 1


def test_installed_pl00_fresh_start_is_coherent() -> None:
    cell = PROJECT_ROOT / "states" / "planner_loyal" / "cells" / "pl00"
    if not (cell / "cell.pst").is_file():
        pytest.skip("pl00 C-RE1 fresh start not installed on this machine")
    meta = json.loads((cell / "meta.json").read_text(encoding="utf-8"))
    assert meta["checkpoint_index"] == 0
    assert meta["checkpoint_id"] == "fresh_start_105"
    assert meta["planner_step_index"] == -1
    assert meta["training_start"] is True
    assert meta["room_id"] == "105"
    assert meta["runtime"] == "recomp"
    assert meta["graft_pst"] is True
    import hashlib

    assert meta["state_sha256"] == hashlib.sha256((cell / "cell.pst").read_bytes()).hexdigest()
    assert seek_index_after_cell(meta) == 0


def _write_cell(root: Path, idx: int, *, meta: dict | None = None) -> None:
    slot = root / "states" / "planner_loyal" / "cells" / cell_dir_name(idx)
    slot.mkdir(parents=True, exist_ok=True)
    (slot / CELL_STATE_NAME).write_bytes(b"STATE")
    (slot / CELL_SIDECAR_NAME).write_text("{}", encoding="utf-8")
    payload = {"checkpoint_index": idx, "checkpoint_id": f"cell_{idx}"}
    if meta:
        payload.update(meta)
    (slot / "meta.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_training_starts_are_pl06_and_every_later_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_pin_env(monkeypatch)
    monkeypatch.delenv("RE1_PLANNER_CHUNK", raising=False)
    monkeypatch.delenv("RE1_RECOMP_CELLS", raising=False)
    _write_cell(tmp_path, 4, meta={"training_start": False})
    # Legacy BizHawk pl05 (old lockpick tip) without the flag is archive-only.
    _write_cell(tmp_path, 5, meta={})
    _write_cell(tmp_path, 6, meta={"training_start": True})
    _write_cell(tmp_path, 10, meta={"training_start": True})
    # Synced cells often drop the flag; slot index still qualifies.
    _write_cell(tmp_path, 11, meta={})
    _write_cell(tmp_path, 18, meta={"chunk_final": True, "training_start": False})

    starts = iter_training_start_cells(tmp_path)
    # Live chemical tail still has steps after pl18, so it stays in the pool.
    assert [int(row["checkpoint_index"]) for row in starts] == [6, 10, 11, 18]


def test_pl00_fresh_start_flag_counts_below_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_pin_env(monkeypatch)
    monkeypatch.delenv("RE1_PLANNER_CHUNK", raising=False)
    monkeypatch.delenv("RE1_RECOMP_CELLS", raising=False)
    _write_cell(
        tmp_path,
        0,
        meta={
            "checkpoint_id": "fresh_start_105",
            "planner_step_index": -1,
            "training_start": True,
        },
    )
    _write_cell(tmp_path, 3, meta={"training_start": False})
    _write_cell(tmp_path, 6, meta={})
    starts = iter_training_start_cells(tmp_path)
    assert [int(row["checkpoint_index"]) for row in starts] == [0, 6]
    assert starts[0]["training_start"] is True
    assert seek_index_after_cell(starts[0]) == 0
    # Pin INDEX=0 selects the fresh start exclusively.
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_INDEX=0\n")
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [0]
    pick = sample_training_start_cell(tmp_path, rng=random.Random(1))
    assert pick is not None and pick["cell_dir"].name == "pl00"


def test_pl00_opening_chunk_stays_a_start_under_live_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pl00 is opening_to_lockpick; live default is cp05_shield_key."""
    _clear_pin_env(monkeypatch)
    monkeypatch.delenv("RE1_PLANNER_CHUNK", raising=False)
    monkeypatch.delenv("RE1_RECOMP_CELLS", raising=False)
    _write_cell(
        tmp_path,
        0,
        meta={
            "checkpoint_id": "fresh_start_105",
            "planner_step_index": -1,
            "training_start": True,
            "chunk_id": "opening_to_lockpick",
        },
    )
    _write_cell(tmp_path, 6, meta={"chunk_id": "cp05_shield_key"})
    starts = iter_training_start_cells(tmp_path)
    assert [int(row["checkpoint_index"]) for row in starts] == [0, 6]
    opening = chunk_path_for_id("opening_to_lockpick", PROJECT_ROOT)
    assert opening is not None
    assert opening.name == "opening_to_lockpick.json"


def test_pinned_index_below_tip_is_a_legal_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_pin_env(monkeypatch)
    monkeypatch.delenv("RE1_PLANNER_CHUNK", raising=False)
    _write_cell(tmp_path, 2, meta={})
    _write_cell(tmp_path, 6, meta={})
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [6]
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_INDEX=2\n")
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [2]


def test_pl00_recomp_meta_is_not_a_bizhawk_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pl00 meta describes cell.pst; a BizHawk worker sharing the dir must skip it."""
    _clear_pin_env(monkeypatch)
    monkeypatch.delenv("RE1_PLANNER_CHUNK", raising=False)
    monkeypatch.delenv("RE1_RECOMP_CELLS", raising=False)
    _write_cell(
        tmp_path,
        0,
        meta={"planner_step_index": -1, "training_start": True, "runtime": "recomp"},
    )
    _write_cell(tmp_path, 6, meta={})
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [6]
    # The C-RE1 worker sees cell.pst and takes it.
    slot = tmp_path / "states" / "planner_loyal" / "cells" / "pl00"
    (slot / "cell.pst").write_bytes(b"PST")
    (tmp_path / "states" / "planner_loyal" / "cells" / "pl06" / "cell.pst").write_bytes(b"PST")
    monkeypatch.setenv("RE1_RECOMP_CELLS", "1")
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [0, 6]


def _write_chunk(tmp_path: Path, chunk_id: str, edges: list[str]) -> Path:
    chunk = tmp_path / f"{chunk_id}.json"
    chunk.write_text(
        json.dumps(
            {
                "chunk_id": chunk_id,
                "steps": [
                    {"n": i + 1, "op": "traverse", "edge_id": edge}
                    for i, edge in enumerate(edges)
                ],
            }
        ),
        encoding="utf-8",
    )
    return chunk


def test_exhausted_last_step_cell_is_not_a_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_pin_env(monkeypatch)
    chunk = _write_chunk(tmp_path, "short", ["106->105", "105->104"])
    monkeypatch.setenv("RE1_PLANNER_CHUNK", str(chunk))
    _write_cell(tmp_path, 6, meta={"planner_step_index": None})
    _write_cell(tmp_path, 7, meta={"planner_step_index": 0, "chunk_id": "short"})
    _write_cell(
        tmp_path,
        8,
        meta={"planner_step_index": 1, "chunk_final": True, "chunk_id": "short"},
    )
    starts = iter_training_start_cells(tmp_path)
    assert [int(row["checkpoint_index"]) for row in starts] == [6, 7]
    assert seek_index_after_cell({"planner_step_index": 1, "checkpoint_index": 8}) == 2
    assert cell_has_remaining_planner_step(
        {"planner_step_index": 1, "checkpoint_index": 8}, 2
    ) is False


def test_cells_from_another_chunk_only_qualify_at_the_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_pin_env(monkeypatch)
    live = _write_chunk(tmp_path, "cp05_shield_key", ["106->105", "105->104", "104->105"])
    monkeypatch.setenv("RE1_PLANNER_CHUNK", str(live))
    # Opening-chunk mints: mid cells never leak into the live pool even with
    # training_start true; the pl06 final mint is the live tip with seek 0.
    for idx in (1, 2, 3, 4, 5):
        _write_cell(
            tmp_path,
            idx,
            meta={
                "planner_step_index": idx - 1,
                "chunk_id": "opening_to_lockpick",
                "training_start": True,
            },
        )
    _write_cell(
        tmp_path,
        6,
        meta={
            "planner_step_index": 5,
            "chunk_id": "opening_to_lockpick",
            "chunk_final": True,
            "training_start": True,
        },
    )
    _write_cell(tmp_path, 7, meta={"planner_step_index": 0, "chunk_id": "cp05_shield_key"})
    starts = iter_training_start_cells(tmp_path)
    assert [int(row["checkpoint_index"]) for row in starts] == [6, 7]
    assert seek_index_after_cell(starts[0], "cp05_shield_key") == 0
    assert seek_index_after_cell(starts[1], "cp05_shield_key") == 1
    # An explicit pin overrides the chunk filter (seek falls to the slot rule).
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_INDEX=3\n")
    pinned = iter_training_start_cells(tmp_path)
    assert [int(row["checkpoint_index"]) for row in pinned] == [3]
    assert seek_index_after_cell(pinned[0], "cp05_shield_key") == 0
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_INDEX=\n")

    # Under the opening chunk the same tree yields pl01..pl05 (pl06 is final).
    opening = _write_chunk(
        tmp_path,
        "opening_to_lockpick",
        ["105:emblem", "105->104", "104->105", "105->106", "106->203", "203->106"],
    )
    monkeypatch.setenv("RE1_PLANNER_CHUNK", str(opening))
    starts = iter_training_start_cells(tmp_path)
    assert [int(row["checkpoint_index"]) for row in starts] == [1, 2, 3, 4, 5]
    assert [seek_index_after_cell(r, "opening_to_lockpick") for r in starts] == [1, 2, 3, 4, 5]


def test_opening_remints_are_starts_when_their_chunk_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live shield-key must still reset from opening remints (pl01+)."""
    _clear_pin_env(monkeypatch)
    live = _write_chunk(tmp_path, "cp05_shield_key", ["106->105", "105->104"])
    monkeypatch.setenv("RE1_PLANNER_CHUNK", str(live))
    chunks = tmp_path / "data" / "planner_chunks"
    chunks.mkdir(parents=True)
    (chunks / "opening_to_lockpick.json").write_text(
        json.dumps(
            {
                "chunk_id": "opening_to_lockpick",
                "steps": [
                    {"n": 1, "op": "acquire", "pickup_id": "105:emblem:1"},
                    {"n": 2, "op": "traverse", "edge_id": "105->104"},
                    {"n": 3, "op": "traverse", "edge_id": "104->105"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (chunks / "cp05_shield_key.json").write_text(
        live.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _write_cell(
        tmp_path,
        0,
        meta={
            "planner_step_index": -1,
            "chunk_id": "opening_to_lockpick",
            "training_start": True,
        },
    )
    _write_cell(
        tmp_path,
        1,
        meta={
            "planner_step_index": 0,
            "chunk_id": "opening_to_lockpick",
            "training_start": True,
        },
    )
    _write_cell(
        tmp_path,
        2,
        meta={
            "planner_step_index": 1,
            "chunk_id": "opening_to_lockpick",
            "training_start": True,
        },
    )
    _write_cell(tmp_path, 6, meta={"chunk_id": "cp05_shield_key"})
    starts = iter_training_start_cells(tmp_path)
    assert [int(row["checkpoint_index"]) for row in starts] == [0, 1, 2, 6]


def _clear_pin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "RE1_PLANNER_RESET_PIN_INDEX",
        "RE1_PLANNER_RESET_PIN_RANGE",
        "RE1_PLANNER_RESET_PIN_SET",
        "RE1_PLANNER_RESET_PIN_SET_WEIGHT",
        "RE1_PLANNER_RESET_PIN_WEIGHTS",
        "RE1_PLANNER_RESET_PIN_FILE",
    ):
        monkeypatch.delenv(key, raising=False)


def _write_pin(root: Path, text: str) -> Path:
    path = root / "data" / "planner_loyal_reset_pin.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _seed_starts(tmp_path: Path) -> None:
    for idx in (6, 10, 11, 13):
        _write_cell(tmp_path, idx)


def test_reset_pin_file_index_and_hot_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_pin_env(monkeypatch)
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
    _clear_pin_env(monkeypatch)
    _seed_starts(tmp_path)
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_SET=6,11\n")
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [
        6,
        11,
    ]
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_INDEX=99\n")
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [
        6,
        10,
        11,
        13,
    ]


def _sample_counts(root: Path, n: int = 400) -> Counter:
    rng = random.Random(0)
    counts: Counter = Counter()
    for _ in range(n):
        pick = sample_training_start_cell(root, rng=rng)
        assert pick is not None
        counts[int(pick["checkpoint_index"])] += 1
    return counts


def test_reset_pin_set_weight_mixes_named_and_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_pin_env(monkeypatch)
    _seed_starts(tmp_path)
    _write_pin(
        tmp_path,
        "RE1_PLANNER_RESET_PIN_SET=11\n"
        "RE1_PLANNER_RESET_PIN_SET_WEIGHT=0.5\n",
    )
    assert [int(row["checkpoint_index"]) for row in iter_training_start_cells(tmp_path)] == [
        6,
        10,
        11,
        13,
    ]
    counts = _sample_counts(tmp_path)
    assert 150 <= counts[11] <= 250
    assert counts[6] > 20 and counts[10] > 20 and counts[13] > 20


def test_reset_pin_weights_rest_and_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_pin_env(monkeypatch)
    _seed_starts(tmp_path)
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_WEIGHTS=11:50,rest:50\n")
    counts = _sample_counts(tmp_path)
    assert 150 <= counts[11] <= 250
    assert counts[6] > 20 and counts[10] > 20 and counts[13] > 20
    _write_pin(tmp_path, "RE1_PLANNER_RESET_PIN_WEIGHTS=6:1,11:1\n")
    exclusive = _sample_counts(tmp_path, n=80)
    assert set(exclusive) == {6, 11}


def test_recomp_mint_replacement_keeps_bizhawk_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from re1_rl.planner_loyal_cells import _preserve_foreign_state

    monkeypatch.setenv("RE1_RECOMP_CELLS", "1")
    dest = tmp_path / "pl07"
    dest.mkdir()
    (dest / CELL_STATE_NAME).write_bytes(b"BIZHAWK")
    (dest / "cell.pst").write_bytes(b"OLD_PST")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "cell.pst").write_bytes(b"NEW_PST")
    _preserve_foreign_state(dest, staging)
    assert (staging / CELL_STATE_NAME).read_bytes() == b"BIZHAWK"
    assert (staging / "cell.pst").read_bytes() == b"NEW_PST"
    assert not (dest / CELL_STATE_NAME).exists()


def test_planner_loyal_quality_drops_path_kills_dim() -> None:
    clean = (96, 75, 33, 11, 1, 0, -30, -40)
    assert lift_planner_loyal_quality(clean) == (96, 75, 33, 11, 1, 0, -30, -40)
    # 9-tuple with kills at index 2 (local remints).
    assert lift_planner_loyal_quality((96, 75, 87, 33, 11, 1, 0, -30, -40)) == (
        96,
        75,
        33,
        11,
        1,
        0,
        -30,
        -40,
    )
    # 9-tuple with kills at index 1 (shipped insert).
    assert lift_planner_loyal_quality((96, 4, 75, 33, 11, 1, 0, -30, -40)) == (
        96,
        75,
        33,
        11,
        1,
        0,
        -30,
        -40,
    )
    # Truncated 8-tuple still carrying the insert (poison no longer at index 4).
    assert lift_planner_loyal_quality((96, 75, 3, 33, 11, 1, 0, -30)) == (
        96,
        75,
        33,
        11,
        1,
        0,
        -30,
        -LEG_FRAMES_SENTINEL,
    )
    # Fat kill dim must not beat a same HP/ammo remint.
    assert planner_loyal_quality_beats(clean, (96, 75, 87, 33, 11, 1, 0, -30, -40)) is False
    assert planner_loyal_quality_beats((96, 80, 33, 11, 1, 0, -30, -40), clean) is True


def test_planner_loyal_kill_audit_prefers_live_then_claim() -> None:
    progress = ProgressTracker()
    progress.note_leg_kills("103", 2)
    progress.note_almanac_kill("103", "zombie", 1)
    progress.note_almanac_kill("108", "dog", 2)
    live = planner_loyal_kill_audit(
        progress,
        predecessor_almanac={"108": {"dog": 2}},
    )
    assert live["paid_stretch"] == 2
    assert live["paid_stretch_by_room"] == {"103": 2}
    assert live["paid_episode"] == 2
    assert live["almanac_stretch"] == 1
    assert live["almanac_stretch_by_room"] == {"103": {"zombie": 1}}
    assert live["almanac_total"] == 3

    progress.claim_checkpoint_success()
    claimed = planner_loyal_kill_audit(progress)
    assert claimed["paid_stretch"] == 2
    assert claimed["paid_stretch_by_room"] == {"103": 2}
    assert claimed["paid_episode"] == 2

    close_planner_loyal_stretch(progress)
    closed = planner_loyal_kill_audit(progress)
    assert closed["paid_stretch"] == 0
    assert closed["paid_episode"] == 2
    assert closed["almanac_total"] == 3
