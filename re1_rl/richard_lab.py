"""Read-only Richard lab countdown state after the 20D cutscene.

Live hunt on QuickSave8 (2026-08-03): Richard's cutscene deposits Jill in room
204 and opens the STATUS/ECG screen with the lab countdown running
(``gs=0x40808200``, ``mode=0x40``). The generic pause-menu controller dismissal
handles that screen; production code must not alter the timer or gameplay RAM.
"""

from __future__ import annotations

from typing import Any

from re1_rl.memory_map import (
    GAME_MODE,
    GAME_STATE,
    LAB_TIMER,
    RICHARD_LAB_COUNTDOWN_STATUS_GAME_MODE,
    RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE,
)

_RAM_FIELDS = [
    ("game_mode", GAME_MODE, "u8"),
    ("game_state", GAME_STATE, "u32"),
    ("lab_timer", LAB_TIMER, "u16"),
]


def richard_lab_countdown_screen_from_ram(ram: dict[str, int | float]) -> bool:
    """True on the post-Richard STATUS screen with the lab countdown overlay."""
    return (
        int(ram.get("game_mode", 0)) == RICHARD_LAB_COUNTDOWN_STATUS_GAME_MODE
        and int(ram.get("game_state", 0)) == RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE
    )


def richard_lab_timer_active(ram: dict[str, int | float]) -> bool:
    """True while the lab countdown byte is armed (non-zero)."""
    return int(ram.get("lab_timer", 0) or 0) > 0


def read_richard_lab_ram(client: Any) -> dict[str, int]:
    raw = client.read_ram(_RAM_FIELDS)
    return {k: int(v) for k, v in raw.items()}
