"""Attack log context helpers (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.attack_log_context import (
    build_attack_log_context,
    format_attack_context_line,
    room_display_name,
    room_roster_summary,
)
from re1_rl.enemy_combat import (
    enemy_combat_events,
    format_enemy_table,
)


def test_room_display_name_tea_room() -> None:
    assert room_display_name("104") == "TEA ROOM"


def test_room_roster_summary() -> None:
    summary = room_roster_summary("107")
    assert "zombie" in summary
    assert "crow" in summary


def test_format_enemy_table() -> None:
    s = format_enemy_table([
        {"slot": 1, "hp": 61},
        {"slot": 0, "hp": 19},
    ])
    assert s == "s0:hp19 s1:hp61"


def test_enemy_combat_events_chip_and_kill() -> None:
    prev = [{"slot": 0, "hp": 61}, {"slot": 1, "hp": 40}]
    curr = [{"slot": 0, "hp": 53}, {"slot": 1, "hp": 0}]
    events = enemy_combat_events(prev, curr)
    assert len(events) == 2
    assert events[0] == {
        "slot": 0,
        "hp_before": 61,
        "hp_after": 53,
        "damage": 8,
        "killed": False,
    }
    assert events[1]["slot"] == 1
    assert events[1]["killed"] is True
    assert events[1]["damage"] == 40


def test_build_attack_log_context_hit() -> None:
    prev = {
        "room_id": "104",
        "hp": 96,
        "x": 4400,
        "z": 2300,
        "enemies": [{"slot": 0, "hp": 61}],
    }
    state = {
        "room_id": "104",
        "hp": 96,
        "x": 4400,
        "z": 2300,
        "facing": 512,
        "cam_id": 3,
        "in_control": True,
        "equipped_weapon_id": 0x01,
        "enemies": [{"slot": 0, "hp": 53}],
        "combat_events": enemy_combat_events(prev["enemies"], [{"slot": 0, "hp": 53}]),
    }
    ctx = build_attack_log_context(prev, state)
    assert ctx["room_name"] == "TEA ROOM"
    assert ctx["room_roster"] == "zombie×1"
    assert "s0:zombie:61->53" in ctx["combat_summary"]
    line = format_attack_context_line(ctx)
    assert "[attack_ctx]" in line
    assert "TEA ROOM" in line
    assert "before=[s0:hp61]" in line
    assert "after=[s0:hp53]" in line
