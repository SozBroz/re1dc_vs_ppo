"""Planner-loyal auto march: roll forward on HP/ammo/time, nothing else."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from re1_rl.planner_march import (
    _march_qualifies,
    maybe_advance_planner_march,
)

Q_GOOD = [96, 0, 45, 100, 4, 1, 0, -30, -49, 0, 0]
Q_BETTER = [96, 0, 45, 100, 4, 1, 0, -30, -36, 0, 735]


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


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, q_next: list[int], march: str = "1"
) -> None:
    monkeypatch.delenv("RE1_YAWN_CELL_PREFIX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RAILS_ROOT", raising=False)
    monkeypatch.delenv("RE1_PLANNER_MARCH", raising=False)
    monkeypatch.delenv("RE1_PLANNER_MARCH_STOP", raising=False)
    monkeypatch.delenv("RE1_PLANNER_MARCH_MIN_S", raising=False)
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    _write_cell(tmp_path, 2, Q_GOOD)
    _write_cell(tmp_path, 3, q_next)
    _write_manifest(tmp_path, {2: Q_GOOD, 3: q_next})
    _write_pin(
        tmp_path,
        "# March mode: 100% starts on pl02.\nRE1_PLANNER_RESET_PIN_INDEX=2\n",
    )
    if march is not None:
        with open(tmp_path / "data" / "planner_loyal_reset_pin.env", "a", encoding="utf-8") as f:
            f.write(f"RE1_PLANNER_MARCH={march}\n")


def test_march_qualifies_dims() -> None:
    assert _march_qualifies(Q_BETTER, Q_GOOD) is True
    assert _march_qualifies(Q_GOOD, Q_GOOD) is True  # equal rolls
    worse_hp = list(Q_BETTER)
    worse_hp[0] = 95
    assert _march_qualifies(worse_hp, Q_GOOD) is False
    worse_ammo = list(Q_BETTER)
    worse_ammo[2] = 44
    assert _march_qualifies(worse_ammo, Q_GOOD) is False
    slower = list(Q_BETTER)
    slower[8] = -50
    assert _march_qualifies(slower, Q_GOOD) is False
    assert _march_qualifies([96, 0, 45], Q_GOOD) is False  # short never qualifies
    assert _march_qualifies(Q_BETTER, [96, 0, 45]) is False


def test_advance_crystalizes_and_rewrites_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_BETTER)
    rec = maybe_advance_planner_march(tmp_path)
    assert rec == {"advanced": True, "from_pin": 2, "to_pin": 3}
    text = (tmp_path / "data" / "planner_loyal_reset_pin.env").read_text(encoding="utf-8")
    assert "RE1_PLANNER_RESET_PIN_INDEX=3" in text
    crystal = tmp_path / "backups" / "Crystals_in_time" / "planner_rooms" / "pl03_solo"
    assert (crystal / "cell.pst").is_file()
    cmeta = json.loads((crystal / "crystal_meta.json").read_text(encoding="utf-8"))
    assert cmeta["to_pl_slot"] == 3 and cmeta["from_pl_slot"] == 2


def test_no_advance_when_worse_in_one_dim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worse = list(Q_BETTER)
    worse[2] = 30
    _setup(tmp_path, monkeypatch, q_next=worse)
    assert maybe_advance_planner_march(tmp_path) is None
    text = (tmp_path / "data" / "planner_loyal_reset_pin.env").read_text(encoding="utf-8")
    assert "RE1_PLANNER_RESET_PIN_INDEX=2" in text


def test_no_advance_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_BETTER, march="0")
    assert maybe_advance_planner_march(tmp_path) is None


def test_no_advance_when_hunted_bytes_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_BETTER)
    # Row exists but live bytes differ -> stale row, hold pin.
    (tmp_path / "states" / "planner_loyal" / "cells" / "pl03" / "cell.pst").write_bytes(b"OTHER")
    assert maybe_advance_planner_march(tmp_path) is None


def test_no_advance_on_env_sourced_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_BETTER)
    (tmp_path / "data" / "planner_loyal_reset_pin.env").write_text(
        "RE1_PLANNER_MARCH=1\n", encoding="utf-8"
    )
    monkeypatch.setenv("RE1_PLANNER_RESET_PIN_FILE", "data/planner_loyal_reset_pin.env")
    monkeypatch.setenv("RE1_PLANNER_RESET_PIN_INDEX", "2")
    assert maybe_advance_planner_march(tmp_path) is None


def test_stop_bound_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, q_next=Q_BETTER)
    with open(tmp_path / "data" / "planner_loyal_reset_pin.env", "a", encoding="utf-8") as f:
        f.write("RE1_PLANNER_MARCH_STOP=2\n")
    assert maybe_advance_planner_march(tmp_path) is None
