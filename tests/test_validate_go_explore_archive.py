"""Offline tests for validate_go_explore_archive (no BizHawk)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from validate_go_explore_archive import validate_archive  # noqa: E402


def _write_cell(bundle_dir: Path, *, state: bytes, side: bytes, good_sha: bool = True) -> dict:
    from re1_rl.go_explore_capture import CELL_META_NAME, CELL_SIDECAR_NAME, CELL_STATE_NAME
    from re1_rl.pb_bundle_io import sha256_file

    bundle_dir.mkdir(parents=True, exist_ok=True)
    state_p = bundle_dir / CELL_STATE_NAME
    side_p = bundle_dir / CELL_SIDECAR_NAME
    state_p.write_bytes(state)
    side_p.write_bytes(side)
    state_sha = sha256_file(state_p)
    side_sha = sha256_file(side_p)
    if not good_sha:
        state_sha = "0" * 64
    meta = {
        "record_id": bundle_dir.name,
        "state_sha256": state_sha,
        "sidecar_sha256": side_sha,
    }
    (bundle_dir / CELL_META_NAME).write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def test_validate_archive_pass_and_fail(tmp_path: Path) -> None:
    from re1_rl.go_explore_archive import GoExploreArchive
    from re1_rl.milestone_digest import cell_key_v2

    root = tmp_path / "go_explore"
    archive_path = root / "archive.json"
    archive = GoExploreArchive(archive_path)

    rid_ok = "aaa111"
    rid_bad = "bbb222"
    key_ok = cell_key_v2("20E", 1000, 2000, "got:lockpick")
    key_bad = cell_key_v2("105", 0, 0, "")

    _write_cell(root / "cells" / rid_ok, state=b"STATE_OK", side=b'{"ok":true}')
    _write_cell(
        root / "cells" / rid_bad,
        state=b"STATE_BAD",
        side=b'{"ok":false}',
        good_sha=False,
    )

    assert archive.upsert(
        room_id="20E",
        x=1000,
        z=2000,
        digest="got:lockpick",
        quality=(100, 1, 0, 2, 1),
        bundle_path=f"cells/{rid_ok}/cell.State",
        record_id=rid_ok,
    ) is not None
    assert archive.upsert(
        room_id="105",
        x=0,
        z=0,
        digest="",
        quality=(50, 0, 0, 1, 1),
        bundle_path=f"cells/{rid_bad}/cell.State",
        record_id=rid_bad,
    ) is not None
    archive.save()

    report = validate_archive(archive_path)
    assert report.n_total == 2
    assert report.n_pass == 1
    assert abs(report.pass_rate - 0.5) < 1e-9
    by_key = {r.cell_key: r for r in report.results}
    assert by_key[key_ok].ok
    assert not by_key[key_bad].ok
    assert by_key[key_bad].reason == "state_sha_mismatch"
