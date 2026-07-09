"""Offline tests for inventory pickup detection (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from log_door_transitions import detect_pickups  # noqa: E402


def test_new_item_only_once():
    prev = {}
    inv = {"emblem": 1}
    held: set[str] = set()
    ev = detect_pickups(prev, inv, held)
    assert len(ev) == 1 and ev[0]["kind"] == "new_item"


def test_beretta_ammo_stack():
    prev = {"beretta": 15, "knife": 1}
    inv = {"beretta": 30, "knife": 1}
    held = {"beretta", "knife"}
    ev = detect_pickups(prev, inv, held)
    assert len(ev) == 1
    assert ev[0]["kind"] == "ammo_stack"
    assert ev[0]["qty_delta"] == 15
    assert ev[0]["ground_item"] == "clip"


def test_no_log_on_ammo_decrease():
    prev = {"beretta": 30}
    inv = {"beretta": 29}
    held = {"beretta"}
    assert detect_pickups(prev, inv, held) == []


def test_no_ammo_stack_on_first_beretta_frame():
    """New beretta must not also emit a stack event from 0 -> N."""
    prev = {}
    inv = {"beretta": 15}
    held: set[str] = set()
    ev = detect_pickups(prev, inv, held)
    assert len(ev) == 1 and ev[0]["kind"] == "new_item"


def test_rewithdraw_from_box_not_ammo_stack():
    """Item reappears after banking: not in prev_qty -> no false stack."""
    prev = {"knife": 1}
    inv = {"knife": 1, "beretta": 30}
    held = {"knife", "beretta"}
    ev = detect_pickups(prev, inv, held)
    assert ev == []


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
