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
    purge_orphan_cell_dirs,
    quality_replace_significant,
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
    assert q[3] == 4  # ever_held-style distinct items (inventory fallback)
    assert q[4] == 1  # not poisoned
    q_poison = compute_quality(_good_state(poisoned=True))
    assert q_poison[4] == 0


def test_compute_quality_slots_uses_ever_held_not_inventory() -> None:
    state = _good_state(
        inventory=["beretta"],
        inventory_slots=[("beretta", 15)],
    )
    q = compute_quality(
        state,
        ever_held={"beretta", "emblem", "knife", "music_notes"},
    )
    assert q[3] == 4


def test_maybe_capture_writes_bundle_and_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.setenv("RE1_GO_CANONICAL_STORE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "500")
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
        env_step=100,
        capture_state={"last_capture_step": -10**9},
    )
    assert proposal is not None
    assert proposal["cell_key"].startswith("v2|r=20E|")
    assert proposal["record_id"]
    assert proposal["quality"] == list(
        compute_quality(state, ever_held={"shield_key", "lockpick"})
    )
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
    monkeypatch.setenv("RE1_GO_CANONICAL_STORE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "500")
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
        env_step=100,
        capture_state={"last_capture_step": -10**9},
    )
    assert first is not None
    second = maybe_capture_cell(
        strong,
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=200,
        capture_state={"last_capture_step": 100},
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
        env_step=300,
        capture_state={"last_capture_step": 200},
    )
    assert third is None


def test_capture_cooldown_blocks_rapid_new_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.setenv("RE1_GO_CANONICAL_STORE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "500")
    monkeypatch.setenv("RE1_GO_CAPTURE_COOLDOWN_STEPS", "60")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    state_a = _good_state(room_id="105", x=100, z=200)
    state_b = _good_state(room_id="106", x=500, z=600)

    def _save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"STATE")

    cap_state = {"last_capture_step": -10**9}
    first = maybe_capture_cell(
        state_a,
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=10,
        capture_state=cap_state,
    )
    assert first is not None
    blocked = maybe_capture_cell(
        state_b,
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=20,
        capture_state=cap_state,
    )
    assert blocked is None


def test_quality_replace_requires_significant_gain() -> None:
    assert quality_replace_significant((50, 0, 0, 0, 1), (40, 0, 0, 0, 1))
    assert not quality_replace_significant((42, 0, 0, 0, 1), (40, 0, 0, 0, 1))


def test_purge_orphan_cell_dirs(tmp_path: Path) -> None:
    root = tmp_path / "cells"
    orphan = root / "deadbeef"
    staging = orphan / ".staging"
    staging.mkdir(parents=True)
    (staging / CELL_STATE_NAME).write_bytes(b"x")
    good = root / "goodid"
    good.mkdir()
    (good / CELL_STATE_NAME).write_bytes(b"s")
    (good / CELL_SIDECAR_NAME).write_text("{}", encoding="utf-8")
    removed = purge_orphan_cell_dirs(root)
    assert removed == 1
    assert not orphan.exists()
    assert good.is_dir()


def test_maybe_capture_skips_on_disk_full_mkdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.setenv("RE1_GO_CANONICAL_STORE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "500")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")

    def _save(path: Path) -> None:
        path.write_bytes(b"STATE")

    real_mkdir = Path.mkdir

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        if ".capture_staging_" in self.as_posix():
            raise OSError(112, "There is not enough space on the disk")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _boom)
    out = maybe_capture_cell(
        _good_state(),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=100,
        capture_state={"last_capture_step": -10**9},
    )
    assert out is None


def test_off_path_room_skipped_when_allowlist_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from re1_rl.milestone_digest import YAWN_PATH_ROOMS

    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    archive = GoExploreArchive(tmp_path / "archive.json")
    out = maybe_capture_cell(
        _good_state(room_id="300"),
        ProgressTracker(),
        archive,
        save_state=lambda p: p.write_bytes(b"x"),
        project_root=tmp_path,
        path_rooms=YAWN_PATH_ROOMS,
    )
    assert out is None


def test_ephemeral_capture_no_cells_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.delenv("RE1_GO_CANONICAL_STORE", raising=False)
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "50")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")

    def _save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"FAKE_STATE")

    proposal = maybe_capture_cell(
        _good_state(),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=50,
        capture_state={"last_capture_step": -10**9},
    )
    assert proposal is not None
    assert proposal.get("bundle_b64")
    assert not (tmp_path / "data" / "go_explore" / "cells").exists()
    assert proposal["cell_key"] not in archive.cells


def test_ephemeral_proposal_passes_learner_ingest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ephemeral bundle shas must match zip bytes (Windows CRLF temp sidecar)."""
    from re1_rl.go_explore_merge import GoExploreMerge

    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.delenv("RE1_GO_CANONICAL_STORE", raising=False)
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "50")
    archive_path = tmp_path / "data" / "go_explore" / "archive.json"
    archive = GoExploreArchive(archive_path)
    progress = ProgressTracker()
    progress.seed_spawn_room("105")

    def _save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"FAKE_STATE")

    proposal = maybe_capture_cell(
        _good_state(),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=50,
        capture_state={"last_capture_step": -10**9},
    )
    assert proposal is not None
    merge = GoExploreMerge(archive_path)
    assert merge.ingest_proposals([proposal]) == [proposal["record_id"]]


def test_capture_budget_persists_and_caps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "2")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")

    saves = {"n": 0}

    def _save(path: Path) -> None:
        saves["n"] += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"STATE")

    cap_state = {"last_capture_step": -10**9}
    for step in (10, 100):
        state = _good_state(x=100 + step * 50, z=200 + step * 50)
        out = maybe_capture_cell(
            state,
            progress,
            archive,
            save_state=_save,
            ever_held=set(),
            project_root=tmp_path,
            env_step=step,
            capture_state=cap_state,
        )
        assert out is not None
    assert saves["n"] == 2
    blocked = maybe_capture_cell(
        _good_state(x=9000, z=9000),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=200,
        capture_state=cap_state,
    )
    assert blocked is None
    # Day cap: must not call BizHawk save_state again (SPS cliff fix).
    assert saves["n"] == 2


def test_capture_budget_byte_cap_enforced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.setenv("RE1_GO_CANONICAL_STORE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "10")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURE_BYTES_DAY", "50")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")

    def _save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"X" * 40)

    cap_state = {"last_capture_step": -10**9}
    out = maybe_capture_cell(
        _good_state(x=100, z=200),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=10,
        capture_state=cap_state,
    )
    assert out is None
    budget_path = tmp_path / "data" / "go_explore" / "capture_budget.json"
    if budget_path.is_file():
        row = json.loads(budget_path.read_text())
        assert int(row.get("captures", 0)) == 0


def test_capture_budget_corrupt_file_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "10")
    budget = tmp_path / "data" / "go_explore" / "capture_budget.json"
    budget.parent.mkdir(parents=True, exist_ok=True)
    budget.write_text("{not json", encoding="utf-8")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")

    def _save(path: Path) -> None:
        path.write_bytes(b"STATE")

    out = maybe_capture_cell(
        _good_state(),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=100,
        capture_state={"last_capture_step": -10**9},
    )
    assert out is None


def test_reconcile_archive_missing_bundles(tmp_path: Path) -> None:
    from re1_rl.go_explore_capture import reconcile_archive_missing_bundles

    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    archive.path.parent.mkdir(parents=True, exist_ok=True)
    digest = compute_digest(_good_state(), ProgressTracker(), ever_held=set())
    archive.upsert(
        room_id="105",
        x=100,
        z=200,
        digest=digest,
        quality=(80, 10, 0, 2, 1),
        bundle_path="cells/dead001/cell.State",
        record_id="dead001",
    )
    removed = reconcile_archive_missing_bundles(archive, project_root=tmp_path)
    assert removed == 1
    assert not archive.cells


def test_manifest_dedupe_skips_weaker_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "50")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    digest = compute_digest(_good_state(), progress, ever_held=set())
    key = cell_key_v2("20E", 1000, 2000, digest)
    manifest_index = {
        key: {
            "cell_key": key,
            "record_id": "canonical001",
            "room_id": "20E",
            "quality": [90, 45, 1, 4, 1],
        }
    }
    saves = 0

    def _save(path: Path) -> None:
        nonlocal saves
        saves += 1
        path.write_bytes(b"x")

    out = maybe_capture_cell(
        _good_state(hp=50),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=100,
        capture_state={"last_capture_step": -10**9},
        manifest_index=manifest_index,
    )
    assert out is None
    assert saves == 0


def _budget_captures(tmp_path: Path) -> int:
    budget_path = tmp_path / "data" / "go_explore" / "capture_budget.json"
    if not budget_path.is_file():
        return 0
    return int(json.loads(budget_path.read_text()).get("captures", 0) or 0)


def test_semantic_prefilter_rejects_weaker_at_cap_no_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "50")
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "3")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "1")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    state = _good_state(hp=40)
    digest = compute_digest(state, progress, ever_held=set())
    rows = []
    for i in range(3):
        key = cell_key_v2("20E", 1000 + i * 4096, 2000, digest)
        rows.append(
            {
                "cell_key": key,
                "record_id": f"inc{i}",
                "room_id": "20E",
                "quality": [90, 45, 1, 4, 1],
            }
        )
    manifest_index = {r["cell_key"]: r for r in rows}
    saves = 0

    def _save(path: Path) -> None:
        nonlocal saves
        saves += 1
        path.write_bytes(b"x")

    # Different tile → new cell_key, same semantic bucket, weaker quality.
    out = maybe_capture_cell(
        _good_state(hp=40, x=1000 + 9 * 4096, z=2000),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=100,
        capture_state={"last_capture_step": -10**9},
        manifest_index=manifest_index,
    )
    assert out is None
    assert saves == 0
    assert _budget_captures(tmp_path) == 0


def test_semantic_prefilter_allows_stronger_at_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.delenv("RE1_GO_CANONICAL_STORE", raising=False)
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "50")
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "3")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "1")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    digest = compute_digest(_good_state(), progress, ever_held=set())
    rows = []
    for i in range(3):
        key = cell_key_v2("20E", 1000 + i * 4096, 2000, digest)
        rows.append(
            {
                "cell_key": key,
                "record_id": f"inc{i}",
                "room_id": "20E",
                "quality": [50, 10, 0, 2, 1],
            }
        )
    manifest_index = {r["cell_key"]: r for r in rows}

    def _save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"STATE")

    out = maybe_capture_cell(
        _good_state(hp=99, x=1000 + 9 * 4096, z=2000),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=100,
        capture_state={"last_capture_step": -10**9},
        manifest_index=manifest_index,
    )
    assert out is not None
    assert out.get("bundle_b64")
    assert _budget_captures(tmp_path) == 1


def test_semantic_prefilter_new_digest_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_EXPLORE_CAPTURE", "1")
    monkeypatch.delenv("RE1_GO_CANONICAL_STORE", raising=False)
    monkeypatch.setenv("RE1_GO_MAX_CAPTURES_DAY", "50")
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "3")
    archive = GoExploreArchive(tmp_path / "data" / "go_explore" / "archive.json")
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    # Incumbents use a different digest so the proposal's bucket is empty.
    rows = []
    for i in range(3):
        key = cell_key_v2("20E", 1000 + i * 4096, 2000, "other:digest")
        rows.append(
            {
                "cell_key": key,
                "record_id": f"inc{i}",
                "room_id": "20E",
                "quality": [99, 99, 9, 9, 1],
            }
        )
    manifest_index = {r["cell_key"]: r for r in rows}

    def _save(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"STATE")

    out = maybe_capture_cell(
        _good_state(hp=40),
        progress,
        archive,
        save_state=_save,
        ever_held=set(),
        project_root=tmp_path,
        env_step=100,
        capture_state={"last_capture_step": -10**9},
        manifest_index=manifest_index,
    )
    assert out is not None
