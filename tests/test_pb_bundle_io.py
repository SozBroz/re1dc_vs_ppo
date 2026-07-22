"""Atomic PB champion install, coherence, and sync locks."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.pb_bundle_io import (
    LOCK_NAME,
    acquire_slot_lock,
    bundle_room_matches_sidecar,
    clear_all_champion_locks,
    install_champion_bundle,
    is_slot_locked,
    release_slot_lock,
    verify_champion_bundle,
)
from re1_rl.pb_champion import list_filled_champions, try_replace_champion


def test_install_stamps_matching_bundle_ids(tmp_path: Path) -> None:
    slot = tmp_path / "champions" / "mainhall_typewriter"
    state = tmp_path / "a.State"
    side = tmp_path / "a.sidecar.json"
    state.write_bytes(b"STATE_BYTES")
    side.write_text(json.dumps({"schema_version": 1, "captured_room_id": "106"}), encoding="utf-8")
    bid = install_champion_bundle(
        slot,
        state_src=state,
        sidecar_src=side,
        record={
            "state_path": "states/pb/champions/mainhall_typewriter/champion.State",
            "sidecar_path": "states/pb/champions/mainhall_typewriter/champion.sidecar.json",
            "score": [1, 0, 0, 0, 90],
            "score_version": 2,
            "room_id": "106",
        },
        holder="test",
    )
    assert bid
    assert not is_slot_locked(slot)
    ok, reason = verify_champion_bundle(slot)
    assert ok, reason
    rec = json.loads((slot / "champion.json").read_text(encoding="utf-8"))
    side_out = json.loads((slot / "champion.sidecar.json").read_text(encoding="utf-8"))
    assert rec["bundle_id"] == bid
    assert side_out["bundle_id"] == bid
    assert rec["state_sha256"]
    assert rec["sidecar_sha256"]


def test_verify_rejects_sidecar_only_or_mismatched_ids(tmp_path: Path) -> None:
    slot = tmp_path / "slot"
    slot.mkdir()
    (slot / "champion.State").write_bytes(b"S")
    (slot / "champion.sidecar.json").write_text("{}", encoding="utf-8")
    (slot / "champion.json").write_text(
        json.dumps({"bundle_id": "aaa", "state_path": "x", "sidecar_path": "y"}),
        encoding="utf-8",
    )
    # JSON has bundle_id but sidecar does not → reject (half stamp).
    ok, reason = verify_champion_bundle(slot)
    assert not ok
    assert reason == "bundle_id_mismatch"

    (slot / "champion.sidecar.json").write_text(
        json.dumps({"bundle_id": "aaa"}), encoding="utf-8"
    )
    ok, reason = verify_champion_bundle(slot)
    assert ok, reason

    (slot / "champion.sidecar.json").write_text(
        json.dumps({"bundle_id": "bbb"}), encoding="utf-8"
    )
    ok, reason = verify_champion_bundle(slot)
    assert not ok
    assert reason == "bundle_id_mismatch"

    (slot / "champion.sidecar.json").unlink()
    ok, reason = verify_champion_bundle(slot)
    assert not ok
    assert reason == "missing_sidecar"


def test_lock_blocks_list_filled_and_stale_clears(tmp_path: Path) -> None:
    state = tmp_path / "a.State"
    side = tmp_path / "a.sidecar.json"
    state.write_bytes(b"S")
    side.write_text("{}", encoding="utf-8")
    assert try_replace_champion(
        tmp_path,
        state_path=state,
        sidecar_path=side,
        state={
            "room_id": "106",
            "hp": 100,
            "inventory_slots": [["beretta", 10], ["ink_ribbon", 1]],
            "inventory": ["beretta", "ink_ribbon"],
        },
        room_id="106",
        visited_rooms=("106",),
    )
    assert len(list_filled_champions(tmp_path)) == 1
    cdir = tmp_path / "states" / "pb" / "champions" / "mainhall_typewriter"
    assert acquire_slot_lock(cdir, holder="sync")
    assert is_slot_locked(cdir)
    assert list_filled_champions(tmp_path) == []
    release_slot_lock(cdir)
    assert len(list_filled_champions(tmp_path)) == 1

    assert acquire_slot_lock(cdir, holder="stuck")
    lp = cdir / LOCK_NAME
    # Age the lock into the past so stale cleanup fires.
    past = time.time() - 10_000
    import os

    os.utime(lp, (past, past))
    assert not is_slot_locked(cdir, stale_s=60.0)
    assert clear_all_champion_locks(tmp_path) == 0  # already cleared as stale


def test_bundle_room_match() -> None:
    assert bundle_room_matches_sidecar("106", {"captured_room_id": "106"})
    assert bundle_room_matches_sidecar("106", {"captured_room_id": "0x106"}) is False
    assert bundle_room_matches_sidecar("106", {})  # unknown capture room → allow
    assert not bundle_room_matches_sidecar("105", {"captured_room_id": "106"})


def test_split_state_hash_rejected(tmp_path: Path) -> None:
    slot = tmp_path / "slot"
    state = tmp_path / "a.State"
    side = tmp_path / "a.sidecar.json"
    state.write_bytes(b"GOOD")
    side.write_text(json.dumps({"captured_room_id": "106"}), encoding="utf-8")
    install_champion_bundle(
        slot,
        state_src=state,
        sidecar_src=side,
        record={"state_path": "s", "sidecar_path": "c", "room_id": "106"},
        holder="t",
    )
    # Corrupt State after install → hash mismatch.
    (slot / "champion.State").write_bytes(b"BAD")
    ok, reason = verify_champion_bundle(slot)
    assert not ok
    assert reason == "state_sha_mismatch"
