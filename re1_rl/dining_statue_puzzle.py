"""Dining Room 2F (202) statue push → blue jewel progress.

Live probe 2026-08-01 (QuickSave1 monitor): persistent flag u8@0x800C8702 bit
0x10 clears in QS1 and sets when the balcony statue is knocked down. Active
push uses game_state 0x80800040 + player_anim 0x10 (bar bookcase uses 0x44).
"""

from __future__ import annotations

from typing import Any

DINING_STATUE_ROOM_ID = "202"
DINING_STATUE_FLAG = 0x800C8702
DINING_STATUE_FLAG_MASK = 0x10
DINING_STATUE_REWARD = 4.0

# Active shove (transient); not the bar-bookcase PUSH_GAME_STATE (0x80800044).
DINING_PUSH_GAME_STATE = 0x80800040
DINING_PUSH_ANIM = 0x10


def dining_statue_knocked_from_state(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    if "dining_statue_knocked" in state:
        return bool(state.get("dining_statue_knocked"))
    raw = state.get("dining_statue_flag")
    if raw is None:
        raw = state.get("dining_statue_flag_raw", 0)
    return bool(int(raw or 0) & DINING_STATUE_FLAG_MASK)


def dining_statue_pushing(state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "")) != DINING_STATUE_ROOM_ID:
        return False
    gs = int(state.get("game_state", 0) or 0)
    anim = int(state.get("player_anim", 0) or 0)
    return gs == DINING_PUSH_GAME_STATE and anim == DINING_PUSH_ANIM


def encode_dining_statue_goal(state: dict[str, Any] | None) -> float:
    """Scalar goal feature: 1.0 once the statue has been knocked down."""
    return 1.0 if dining_statue_knocked_from_state(state) else 0.0
