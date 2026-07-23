"""Yawn raw→logical HP translation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memory_map import decode_enemy_table
from re1_rl.yawn_hp import (
    YAWN_LOGICAL_MAX_ATTIC,
    YAWN_RAW_FULL,
    apply_yawn_hp_translate,
    yawn_logical_hp,
)


def test_yawn_logical_full_and_after_four_beretta() -> None:
    assert yawn_logical_hp(YAWN_RAW_FULL) == YAWN_LOGICAL_MAX_ATTIC
    # Firewatch: 3050 -> 3005 (−45 ≈ 4×~11)
    assert yawn_logical_hp(3005) == YAWN_LOGICAL_MAX_ATTIC - 45
    assert yawn_logical_hp(YAWN_RAW_FULL - YAWN_LOGICAL_MAX_ATTIC) == 0


def test_apply_only_in_room_210() -> None:
    ents = [{"slot": 0, "hp": 3050, "type_id": 0x0F}]
    out = apply_yawn_hp_translate(ents, room_id="210")
    assert out[0]["hp"] == 120
    assert out[0]["hp_raw"] == 3050
    assert out[0]["yawn_translated"] is True
    untouched = apply_yawn_hp_translate(ents, room_id="104")
    assert untouched[0]["hp"] == 3050
    assert "yawn_translated" not in untouched[0]


def test_decode_enemy_table_translates_yawn() -> None:
    ram = {
        "stage_id": 1,  # stage digit 2 → room 210
        "room_id": 0x10,
        "player_x": 6100,
        "player_z": 3600,
        "enemy0_hp": 3050,
        "enemy0_type_id": 0x0F,
        "enemy0_x": 4500,
        "enemy0_z": 12000,
        "enemy0_active_byte": 2,
    }
    for i in range(1, 6):
        ram[f"enemy{i}_hp"] = 0
    decoded = decode_enemy_table(ram)
    assert len(decoded) == 1
    assert decoded[0]["hp_raw"] == 3050
    assert decoded[0]["hp"] == 120


def test_tiger_tyrant_not_translated() -> None:
    """Black Tiger ~204 / Tyrant ~220 stay raw (low hundreds)."""
    for room, hp in (("30C", 204), ("513", 220)):
        out = apply_yawn_hp_translate(
            [{"slot": 0, "hp": hp}], room_id=room
        )
        assert out[0]["hp"] == hp
