"""GoExploreMerge: proposal admit / replace on learner archive."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.go_explore_merge import (
    CELL_STATE_NAME,
    GoExploreMerge,
    extract_proposals_from_infos,
    make_cell_bundle_zip,
)
from re1_rl.milestone_digest import cell_key_v2


def _key(room: str = "20E", digest: str = "gallery:idle") -> str:
    return cell_key_v2(room, 4096, 8192, digest)


def _proposal(
    *,
    record_id: str,
    quality: list[int],
    cell_key: str | None = None,
    with_bundle: bool = True,
    worker_id: str = "workhorse1",
) -> dict:
    key = cell_key or _key()
    prop: dict = {
        "cell_key": key,
        "record_id": record_id,
        "quality": quality,
        "worker_id": worker_id,
        "captured_at_step": 100,
    }
    if with_bundle:
        state = b"FAKE_STATE_" + record_id.encode()
        side = {"room_id": "20E", "bundle_id": record_id}
        blob = make_cell_bundle_zip(state_bytes=state, sidecar=side)
        prop["bundle_b64"] = base64.b64encode(blob).decode("ascii")
        prop["state_sha256"] = hashlib.sha256(state).hexdigest()
        prop["sidecar_sha256"] = hashlib.sha256(
            (json.dumps(side, indent=2, sort_keys=True) + "\n").encode("utf-8")
        ).hexdigest()
        # sidecar sha inside zip may differ if json formatting differs — recompute from zip contents
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            prop["sidecar_sha256"] = hashlib.sha256(zf.read("cell.sidecar.json")).hexdigest()
            prop["state_sha256"] = hashlib.sha256(zf.read(CELL_STATE_NAME)).hexdigest()
    return prop


def test_extract_proposals_from_infos() -> None:
    infos = [
        {"episode": 1},
        {"go_explore_capture": [{"record_id": "a", "cell_key": "v2|r=105|x=0|z=0|m=x"}]},
        {"go_explore_capture": {"record_id": "b", "cell_key": "v2|r=105|x=0|z=0|m=x"}},
    ]
    props = extract_proposals_from_infos(infos)
    assert len(props) == 2
    assert props[0]["record_id"] == "a"
    assert props[1]["record_id"] == "b"


def test_admit_new_cell(tmp_path: Path) -> None:
    archive = tmp_path / "archive.json"
    merge = GoExploreMerge(archive)
    prop = _proposal(record_id="rec_a", quality=[8, 12, 2, 3, 0])
    accepted = merge.ingest_proposals([prop])
    assert accepted == ["rec_a"]
    assert merge.archive_version == 1
    assert archive.is_file()
    cell = merge.archive.cells[prop["cell_key"]]
    assert cell.record_id == "rec_a"
    assert cell.quality == (8, 12, 2, 3, 0, 0)
    assert (tmp_path / "cells" / "rec_a" / CELL_STATE_NAME).is_file()


def test_reject_worse_quality(tmp_path: Path) -> None:
    archive = tmp_path / "archive.json"
    merge = GoExploreMerge(archive)
    key = _key()
    merge.ingest_proposals([_proposal(record_id="rec_hi", quality=[9, 10, 1, 2, 0], cell_key=key)])
    v1 = merge.archive_version
    accepted = merge.ingest_proposals(
        [_proposal(record_id="rec_lo", quality=[1, 1, 0, 0, 0], cell_key=key)]
    )
    assert accepted == []
    assert merge.archive_version == v1
    assert merge.archive.cells[key].record_id == "rec_hi"


def test_replace_better_quality(tmp_path: Path) -> None:
    archive = tmp_path / "archive.json"
    merge = GoExploreMerge(archive)
    key = _key()
    merge.ingest_proposals([_proposal(record_id="rec_old", quality=[5, 5, 0, 0, 0], cell_key=key)])
    # Significant ammo gain (noise-thresholded replace, like PB champions).
    accepted = merge.ingest_proposals(
        [_proposal(record_id="rec_new", quality=[5, 20, 0, 0, 0], cell_key=key)]
    )
    assert accepted == ["rec_new"]
    assert merge.archive.cells[key].record_id == "rec_new"
    assert merge.archive.cells[key].quality[1] == 20
    assert not (tmp_path / "cells" / "rec_old").exists()
    assert (tmp_path / "cells" / "rec_new" / CELL_STATE_NAME).is_file()


def test_replace_rejects_hp_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_GO_REPLACE_MIN_HP_DELTA", "8")
    merge = GoExploreMerge(tmp_path / "archive.json")
    key = _key()
    merge.ingest_proposals([_proposal(record_id="rec_old", quality=[5, 5, 0, 0, 0], cell_key=key)])
    accepted = merge.ingest_proposals(
        [_proposal(record_id="rec_new", quality=[6, 5, 0, 0, 0], cell_key=key)]
    )
    assert accepted == []
    assert merge.archive.cells[key].record_id == "rec_old"


def test_manifest_versioning(tmp_path: Path) -> None:
    merge = GoExploreMerge(tmp_path / "archive.json")
    merge.ingest_proposals([_proposal(record_id="r1", quality=[1, 0, 0, 0, 0])])
    man = merge.build_manifest(since_version=0)
    assert man["archive_version"] == 1
    assert len(man["cells"]) == 1
    empty = merge.build_manifest(since_version=1)
    assert empty["archive_version"] == 1
    assert empty["cells"] == []


def test_integrity_rejects_bad_state_sha(tmp_path: Path) -> None:
    merge = GoExploreMerge(tmp_path / "archive.json")
    prop = _proposal(record_id="bad", quality=[3, 0, 0, 0, 0])
    prop["state_sha256"] = "0" * 64
    assert merge.ingest_proposals([prop]) == []
    assert merge.archive.cells == {}


def test_semantic_pose_cap_evicts_to_six(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "6")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "1")
    monkeypatch.setenv("RE1_GO_MAX_CELLS_PER_ROOM", "40")
    merge = GoExploreMerge(tmp_path / "archive.json")
    digest = "gallery:idle"
    props = []
    for i in range(8):
        key = cell_key_v2("20E", 4096 * i, 0, digest)
        # Later poses are stronger so they displace weak early ones.
        props.append(
            _proposal(
                record_id=f"pose{i}",
                quality=[10 + i, 0, 0, 0, 1],
                cell_key=key,
            )
        )
    accepted = merge.ingest_proposals(props)
    assert len(accepted) == 8
    assert len(merge.archive.cells) == 6
    assert merge.evicted == 2
    # Weakest early poses should be gone; strongest remain.
    remaining_ids = {c.record_id for c in merge.archive.cells.values()}
    assert "pose0" not in remaining_ids
    assert "pose1" not in remaining_ids
    assert "pose7" in remaining_ids
    assert not (tmp_path / "cells" / "pose0").exists()
    assert (tmp_path / "cells" / "pose7" / CELL_STATE_NAME).is_file()


def test_global_cap_evicts_weakest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_ARCHIVE_CELLS", "3")
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "10")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "1")
    monkeypatch.setenv("RE1_GO_MAX_CELLS_PER_ROOM", "40")
    merge = GoExploreMerge(tmp_path / "archive.json")
    # Fill archive with 3 weak cells in room 105.
    for i in range(3):
        key = cell_key_v2("105", 4096 * i, 0, f"d{i}")
        assert merge.ingest_proposals(
            [_proposal(record_id=f"old{i}", quality=[5, 0, 0, 0, 1], cell_key=key)]
        ) == [f"old{i}"]
    assert len(merge.archive.cells) == 3
    # Stronger cell in a different room should admit via global eviction.
    new_key = cell_key_v2("106", 0, 0, "fresh")
    accepted = merge.ingest_proposals(
        [_proposal(record_id="new1", quality=[90, 0, 0, 0, 1], cell_key=new_key)]
    )
    assert accepted == ["new1"]
    assert len(merge.archive.cells) == 3
    assert merge.evicted >= 1
    remaining = {c.record_id for c in merge.archive.cells.values()}
    assert "new1" in remaining
    assert len(remaining & {"old0", "old1", "old2"}) == 2
    assert len([p for p in (tmp_path / "cells").iterdir() if p.is_dir()]) == 3


def test_semantic_reject_when_evict_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "2")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "0")
    merge = GoExploreMerge(tmp_path / "archive.json")
    digest = "gallery:idle"
    for i in range(2):
        key = cell_key_v2("20E", 4096 * i, 0, digest)
        merge.ingest_proposals(
            [_proposal(record_id=f"p{i}", quality=[50, 0, 0, 0, 1], cell_key=key)]
        )
    key3 = cell_key_v2("20E", 4096 * 2, 0, digest)
    accepted = merge.ingest_proposals(
        [_proposal(record_id="p2", quality=[99, 0, 0, 0, 1], cell_key=key3)]
    )
    assert accepted == []
    assert len(merge.archive.cells) == 2
    assert merge.rejected_semantic >= 1


def test_room_digest_cap_rejects_new_milestone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "1")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "1")
    monkeypatch.setenv("RE1_GO_MAX_CELLS_PER_ROOM", "2")
    merge = GoExploreMerge(tmp_path / "archive.json")
    for digest, rid in (("digest_a", "cell_a"), ("digest_b", "cell_b")):
        key = cell_key_v2("105", 0, 0, digest)
        assert merge.ingest_proposals(
            [_proposal(record_id=rid, quality=[50, 0, 0, 0, 1], cell_key=key)]
        ) == [rid]
    assert len(merge.archive.cells) == 2
    key_c = cell_key_v2("105", 0, 0, "digest_c")
    assert merge.ingest_proposals(
        [_proposal(record_id="cell_c", quality=[90, 0, 0, 0, 1], cell_key=key_c)]
    ) == []
    assert len(merge.archive.cells) == 2


def test_semantic_replace_same_digest_different_tile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RE1_GO_MAX_POSES_PER_BUCKET", "1")
    monkeypatch.setenv("RE1_GO_POSE_EVICT", "1")
    monkeypatch.setenv("RE1_GO_REPLACE_MIN_HP_DELTA", "5")
    merge = GoExploreMerge(tmp_path / "archive.json")
    digest = "gallery:idle"
    key0 = cell_key_v2("105", 0, 0, digest)
    assert merge.ingest_proposals(
        [_proposal(record_id="weak", quality=[40, 0, 0, 0, 1], cell_key=key0)]
    ) == ["weak"]
    key1 = cell_key_v2("105", 4096, 0, digest)
    assert merge.ingest_proposals(
        [_proposal(record_id="strong", quality=[50, 0, 0, 0, 1], cell_key=key1)]
    ) == ["strong"]
    assert len(merge.archive.cells) == 1
    assert "weak" not in {c.record_id for c in merge.archive.cells.values()}
    assert (tmp_path / "cells" / "weak").exists() is False
    assert (tmp_path / "cells" / "strong" / CELL_STATE_NAME).is_file()
