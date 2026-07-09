"""Unit tests for fresh dining spawn validation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.fresh_spawn import validate_fresh_dining_spawn


def _ram(**kwargs):
    base = {
        "character_id": 1,
        "stage_id": 0,
        "room_id": 5,
        "player_hp": 96,
        "game_mode": 0x80,
        "inv_slot_0": 0x1501,  # knife qty 1
        "inv_slot_1": 0x0F02,  # beretta qty 15
        "inv_slot_2": 0x010B,  # spray qty 1
    }
    base.update(kwargs)
    return base


def test_fresh_dining_ok():
    ok, errs = validate_fresh_dining_spawn(_ram())
    assert ok, errs


def test_rejects_special_key():
    ok, errs = validate_fresh_dining_spawn(_ram(inv_slot_3=0x0138))
    assert not ok
    assert any("special_key" in e for e in errs)


def test_rejects_wrong_room():
    ok, _ = validate_fresh_dining_spawn(_ram(room_id=6))
    assert not ok
