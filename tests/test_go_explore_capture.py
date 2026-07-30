"""Go-Explore capture: integrity, quality, atomic bundle (no BizHawk)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.go_explore_archive import GoExploreArchive
from re1_rl.go_explore_capture import (
    CELL_META_NAME,
    CELL_SIDECAR_NAME,
    CELL_STATE_NAME,
    compute_quality,
    go_explore_capture_enabled,
    integrity_gate_ok,
    maybe_capture_cell,
)
from re1_rl.milestone_digest import cell_key_v2, compute_digest
from re1_rl.progress import ProgressTracker


def _good_state(**overrides: object) -> dict:
    base = {
        "room_id": "20E",
        "x": 1000,
        "z": 2000,
        "hp": 90,
        "in_control": True,
        "dead": False,
        "poisoned": False,
        "inventory": ["beretta", "handgun_bullets", "green_herb", "shield_key"],
        "inventory_slots": [
            ("beretta", 15),
            ("handgun_bullets", 30),
            ("green_herb", 1),
            ("shield_key", 1),
        ],
    }
    base.update(overrides)
    return base


def test_capture_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RE1_GO_EXPLORE_CAPTURE", raising=False)
    assert go_explore_capture_enabled() is False


def test_capture_enabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    assert go_explore_capture_enabled() is True


def test_integrity_gate() -> None:
    progress = ProgressTracker()
    ok, reason = integrity_gate_ok(_good_state(), progress)
    assert ok and reason == "ok"

    ok, reason = integrity_gate_ok(_good_state(in_control=False), progress)
    assert not ok and reason == "not_in_control"

    ok, reason = integrity_gate_ok(_good_state(dead=True), progress)
    assert not ok and reason == "dead"

    progress.kenneth_gate_breached = True
    ok, reason = integrity_gate_ok(_good_state(), progress)
    assert not ok and reason == "kenneth_gate_breached"


def test_compute_quality_tuple() -> None:
    q = compute_quality(_good_state())
    assert q[0] == 90  # hp
    assert q[1] == 15 + 30  # beretta loaded + handgun bullets
    assert q[2] == 1  # green herb
    assert q[3] == 4  # slots
    assert q[4] == 1  # not poisoned
    q_poison = compute_quality(_good_state(poisoned=True))
    assert q_poison[4] == 0


def test_maybe_capture_writes_bundle_and_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    archive_path = tmp_path / "data" / "go_explore" / "archive.json"
    archive = GoExploreArchive(archive_path)
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    state = _good_state()

    def _save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"FAKE_GO_EXPLORE_STATE")

    proposal = maybe_capture_cell(
        state,
        progress,
        archive,
        save_state=_save,
        ever_held={"shield_key", "lockpick"},
        project_root=tmp_path,
    )
    assert proposal is not None
    assert proposal["cell_key"].startswith("v2|r=20E|")
    assert proposal["record_id"]
    assert proposal["quality"] == list(compute_quality(state))
    assert Path(proposal["paths"]["state"]).is_file()
    assert Path(proposal["paths"]["sidecar"]).is_file()
    assert Path(proposal["paths"]["meta"]).is_file()
    assert len(proposal["state_sha256"]) == 64
    assert len(proposal["sidecar_sha256"]) == 64

    digest = compute_digest(state, progress, ever_held={"shield_key", "lockpick"})
    expected_key = cell_key_v2("20E", 1000, 2000, digest)
    assert proposal["cell_key"] == expected_key
    assert expected_key in archive.cells
    assert archive.cells[expected_key].record_id == proposal["record_id"]

    meta = json.loads(Path(proposal["paths"]["meta"]).read_text(encoding="utf-8"))
    assert meta["state_sha256"] == proposal["state_sha256"]
    assert (Path(proposal["paths"]["bundle_dir"]) / CELL_STATE_NAME).is_file()
    assert (Path(proposal["paths"]["bundle_dir"]) / CELL_SIDECAR_NAME).is_file()
    assert (Path(proposal["paths"]["bundle_dir"]) / CELL_META_NAME).is_file()


def test_maybe_capture_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RE1_GO_EXPLORE_CAPTURE", raising=False)
    archive = GoExploreArchive(tmp_path / "archive.json")
    out = maybe_capture_cell(
        _good_state(),
        ProgressTracker(),
        archive,
        save_state=lambda p: p.write_bytes(b"x"),
        project_root=tmp_path,
    )
    assert out is None


def test_maybe_capture_skips_integrity_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    archive = GoExploreArchive(tmp_path / "archive.json")
    out = maybe_capture_cell(
        _good_state(in_control=False),
        ProgressTracker(),
        archive,
        save_state=lambda p: p.write_bytes(b"x"),
        project_root=tmp_path,
    )
    assert out is None


def test_quality_replace_admits_better(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    weak = _good_state(hp=40)
    strong = _good_state(hp=95)

    def _save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"STATE")

    first = maybe_capture_cell(
        weak,
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
    )
    assert first is not None
    second = maybe_capture_cell(
        strong,
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
    )
    assert second is not None
    assert second["quality"][0] == 95
    # Same cell key → quality upgraded.
    assert second["cell_key"] == first["cell_key"]
    assert archive.cells[first["cell_key"]].quality[0] == 95

    # Worse / equal quality is rejected.
    third = maybe_capture_cell(
        _good_state(hp=50),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
    )
    assert third is None


def test_off_path_room_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    archive = GoExploreArchive(tmp_path / "archive.json")
    out = maybe_capture_cell(
        _good_state(room_id="300"),
        ProgressTracker(),
        archive,
        save_state=lambda p: p.write_bytes(b"x"),
        project_root=tmp_path,
    )
    assert out is None
