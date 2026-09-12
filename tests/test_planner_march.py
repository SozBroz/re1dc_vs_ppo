"""Planner-loyal auto march: grind one PL until it beats the champion row."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.planner_march import (
    _champion_qualifies,
    _champions,
    maybe_advance_planner_march,
)

# Champion pl03 (thin): hp=96 kills=0 ammo=45 ... frames=49.
Q_CHAMP = [96, 0, 45, 100, 4, 1, 0, -30, -49, 0, 0]
# Fat remint: same kit, -36 frames.
Q_FAT = [96, 0, 45, 100, 4, 1, 0, -30, -36, 0, 735]


def _write_cell(root: Path, idx: int, quality: list[int]) -> None:
    cell = root / "states" / "planner_loyal" / "cells" / f"pl{idx:02d}"
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "cell.pst").write_bytes(b"STATE_%02d" % idx)
    (cell / "cell.sidecar.json").write_text(
        json.dumps({"checkpoint_index": idx}) + "\n", encoding="utf-8"
    )
    (cell / "meta.json").write_text(
        json.dumps(
            {
                "checkpoint_index": idx,
                "quality": list(quality),
                "state_sha256": "state%02d" % idx,
                "sidecar_sha256": "side%02d" % idx,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_manifest(root: Path, rows: dict[int, list[int]]) -> None:
    import hashlib

    cells = []
    for idx, q in rows.items():
        state_b = b"STATE_%02d" % idx
        side_b = (root / "states" / "planner_loyal" / "cells" / f"pl{idx:02d}" / "cell.sidecar.json").read_bytes()
        cells.append(
            {
                "checkpoint_index": idx,
                "quality": list(q),
                "state_sha256": hashlib.sha256(state_b).hexdigest(),
                "sidecar_sha256": hashlib.sha256(side_b).hexdigest(),
            }
        )
    (root / "states" / "planner_loyal" / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "archive_version": 7, "cells": cells}) + "\n",
        encoding="utf-8",
    )


def _write_pin(root: Path, lines: str) -> Path:
    p = root / "data" / "planner_loyal_reset_pin.env"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(lines, encoding="utf-8")
    return p


def _write_champions(root: Path, rows: dict[int, list[int]]) -> None:
    p = root / "data" / "planner_march_champions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"cells": {str(k): v for k, v in rows.items()}}) + "\n",
        encoding="utf-8",
    )


def _write_state(root: Path, state: dict) -> None:
    import time

    p = root / "data" / "planner_march_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state) + "\n", encoding="utf-8")


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, q_next: list[int], march: str = "1"
) -> None:
    monkeypatch.delenv("RE1_YAWN_CELL_PREFIX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RAILS_ROOT", raising=False)
    monkeypatch.delenv("RE1_PLANNER_MARCH", raising=False)
    monkeypatch.delenv("RE1_PLANNER_MARCH_STOP", raising=False)
    monkeypatch.delenv("RE1_PLANNER_MARCH_MIN_S", raising=False)
    monkeypatch.delenv("RE1_LEARNER_HOST", raising=False)
    monkeypatch.delenv("FLEET_LEARNER_HOST", raising=False)
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    _write_cell(tmp_path, 2, Q_CHAMP)
    _write_cell(tmp_path, 3, q_next)
    _write_manifest(tmp_path, {2: Q_CHAMP, 3: q_next})
    _write_champions(tmp_path, {3: Q_CHAMP})
    _write_pin(
        tmp_path,
        "# March mode: 100% starts on pl02.\nRE1_PLANNER_RESET_PIN_INDEX=2\n",
    )
    if march is not None:
        with open(tmp_path / "data" / "planner_loyal_reset_pin.env", "a", encoding="utf-8") as f:
            f.write(f"RE1_PLANNER_MARCH={march}\n")


def test_champion_gate_dims() -> None:
    assert _champion_qualifies(Q_FAT, Q_CHAMP) is True
    assert _champion_qualifies(Q_CHAMP, Q_CHAMP) is True  # rolled equal counts
    worse_hp = list(Q_FAT)
    worse_hp[0] = 95
    assert _champion_qualifies(worse_hp, Q_CHAMP) is False
    worse_kills = list(Q_FAT)
    worse_kills[1] = -1
    assert _champion_qualifies(worse_kills, Q_CHAMP) is False
    worse_ammo = list(Q_FAT)
    worse_ammo[2] = 44
    assert _champion_qualifies(worse_ammo, Q_CHAMP) is False
    slower = list(Q_FAT)
    slower[8] = -50
    assert _champion_qualifies(slower, Q_CHAMP) is False
    # Better kit in every dim still passes (strictly-better mint).
    better = [96, 1, 46, 100, 4, 1, 0, -30, -36, 0, 735]
    assert _champion_qualifies(better, Q_CHAMP) is True
    assert _champion_qualifies([96, 0, 45], Q_CHAMP) is False  # short never qualifies
    assert _champion_qualifies(Q_FAT, [96, 0, 45]) is False


def test_champions_loader(tmp_path: Path) -> None:
    _write_champions(tmp_path, {3: Q_CHAMP, 4: Q_FAT})
    cells = _champions(tmp_path)
    assert cells[3] == Q_CHAMP and cells[4] == Q_FAT
    assert _champions(tmp_path / "nonexistent") == {}


def _grind_elapsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age_s: float) -> None:
    import time

    monkeypatch.setenv("RE1_PLANNER_MARCH_MIN_S", "600")
    _write_state(
        tmp_path,
        {"pin_idx": 2, "pin_since_unix": time.time() - age_s},
    )


def test_first_sighting_starts_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_FAT)
    monkeypatch.setenv("RE1_PLANNER_MARCH_MIN_S", "600")
    assert maybe_advance_planner_march(tmp_path) is None
    state = json.loads(
        (tmp_path / "data" / "planner_march_state.json").read_text(encoding="utf-8")
    )
    assert state["pin_idx"] == 2 and state["pin_since_unix"] > 0
    text = (tmp_path / "data" / "planner_loyal_reset_pin.env").read_text(encoding="utf-8")
    assert "RE1_PLANNER_RESET_PIN_INDEX=2" in text


def test_min_grind_time_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_FAT)
    _grind_elapsed(tmp_path, monkeypatch, age_s=60.0)  # only 1 of 10 min
    assert maybe_advance_planner_march(tmp_path) is None
    text = (tmp_path / "data" / "planner_loyal_reset_pin.env").read_text(encoding="utf-8")
    assert "RE1_PLANNER_RESET_PIN_INDEX=2" in text


def test_advance_crystalizes_rewrites_pin_and_queues_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_FAT)
    _grind_elapsed(tmp_path, monkeypatch, age_s=601.0)
    rec = maybe_advance_planner_march(tmp_path)
    assert rec is not None and rec["advanced"] is True
    assert (rec["from_pin"], rec["to_pin"]) == (2, 3)
    text = (tmp_path / "data" / "planner_loyal_reset_pin.env").read_text(encoding="utf-8")
    assert "RE1_PLANNER_RESET_PIN_INDEX=3" in text
    crystal = tmp_path / "backups" / "Crystals_in_time" / "planner_rooms" / "pl03_solo"
    assert (crystal / "cell.pst").is_file()
    cmeta = json.loads((crystal / "crystal_meta.json").read_text(encoding="utf-8"))
    assert cmeta["to_pl_slot"] == 3 and cmeta["from_pl_slot"] == 2
    state = json.loads(
        (tmp_path / "data" / "planner_march_state.json").read_text(encoding="utf-8")
    )
    assert state["pin_idx"] == 3
    assert state["pending_reset"]["advance_id"] == rec["advance_id"]


def test_no_advance_when_below_champion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worse = list(Q_FAT)
    worse[2] = 30
    _setup(tmp_path, monkeypatch, q_next=worse)
    _grind_elapsed(tmp_path, monkeypatch, age_s=3600.0)
    assert maybe_advance_planner_march(tmp_path) is None
    text = (tmp_path / "data" / "planner_loyal_reset_pin.env").read_text(encoding="utf-8")
    assert "RE1_PLANNER_RESET_PIN_INDEX=2" in text


def test_no_advance_without_champion_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_FAT)
    (tmp_path / "data" / "planner_march_champions.json").write_text(
        json.dumps({"cells": {}}) + "\n", encoding="utf-8"
    )
    _grind_elapsed(tmp_path, monkeypatch, age_s=3600.0)
    assert maybe_advance_planner_march(tmp_path) is None


def test_no_advance_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_FAT, march="0")
    assert maybe_advance_planner_march(tmp_path) is None


def test_no_advance_when_hunted_bytes_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_FAT)
    _grind_elapsed(tmp_path, monkeypatch, age_s=3600.0)
    # Row exists but live bytes differ -> stale row, hold pin.
    (tmp_path / "states" / "planner_loyal" / "cells" / "pl03" / "cell.pst").write_bytes(b"OTHER")
    assert maybe_advance_planner_march(tmp_path) is None


def test_no_advance_on_env_sourced_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_FAT)
    (tmp_path / "data" / "planner_loyal_reset_pin.env").write_text(
        "RE1_PLANNER_MARCH=1\n", encoding="utf-8"
    )
    monkeypatch.setenv("RE1_PLANNER_RESET_PIN_FILE", "data/planner_loyal_reset_pin.env")
    monkeypatch.setenv("RE1_PLANNER_RESET_PIN_INDEX", "2")
    assert maybe_advance_planner_march(tmp_path) is None


def test_stop_bound_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_FAT)
    with open(tmp_path / "data" / "planner_loyal_reset_pin.env", "a", encoding="utf-8") as f:
        f.write("RE1_PLANNER_MARCH_STOP=2\n")
    assert maybe_advance_planner_march(tmp_path) is None
