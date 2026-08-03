"""Richard lab countdown trap after the 20D serum cutscene.

Live hunt on QuickSave8 (2026-08-03): giving Richard serum plays the cutscene,
deposits Jill in room 204, and opens the STATUS/ECG screen with the lab
countdown running (``gs=0x40808200``, ``mode=0x40``). Start does not close it.

Recovery writes in-control gameplay RAM and sets ``lab_timer=0``, which disables
the countdown for the rest of the run (persists through savestates). A separate
``richard_dead`` scenario flag was not found; ``lab_timer=0`` is the practical
equivalent for training.
"""

from __future__ import annotations

from typing import Any

from re1_rl.game_session import outside_gameplay_reason
from re1_rl.memory_map import (
    GAME_MODE,
    GAME_STATE,
    IN_CONTROL_MASK,
    LAB_TIMER,
    PLAYER_HP,
    RICHARD_LAB_COUNTDOWN_STATUS_GAME_MODE,
    RICHARD_LAB_COUNTDOWN_STATUS_GAME_STATE,
    player_died,
)
from re1_rl.ram_skip import in_control_from_ram

SETTLE_FRAMES = 4
IN_CONTROL_GAME_STATE = 0x80800004

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


def clear_richard_lab_countdown(
    client: Any,
    *,
    prev_hp: int = 0,
    episode_start_hp: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Dismiss the post-Richard STATUS trap and disable the lab countdown.

    Returns ``(still_trapped, frames_used, report)``. ``still_trapped=False``
    means Jill is in active gameplay with ``lab_timer=0``.
    """
    report: dict[str, Any] = {"path": "ram_dismiss_lab0", "cleared": False}
    ram = read_richard_lab_ram(client)
    if not richard_lab_countdown_screen_from_ram(ram):
        if richard_lab_timer_active(ram) and in_control_from_ram(ram):
            client.write_ram([("lab_timer", LAB_TIMER, "u16", 0)])
            client.frameadvance(SETTLE_FRAMES)
            ram = read_richard_lab_ram(client)
            report["path"] = "lab0_only"
        report["cleared"] = not richard_lab_timer_active(ram)
        report["lab_timer"] = int(ram.get("lab_timer", 0))
        return False, 0, report

    client.write_ram(
        [
            ("game_mode", GAME_MODE, "u8", IN_CONTROL_MASK),
            ("game_state", GAME_STATE, "u32", IN_CONTROL_GAME_STATE),
            ("lab_timer", LAB_TIMER, "u16", 0),
        ]
    )
    client.frameadvance(SETTLE_FRAMES)
    frames = SETTLE_FRAMES

    ram = read_richard_lab_ram(client)
    hp = int(client.read_ram([("player_hp", PLAYER_HP, "u16")])["player_hp"])
    if player_died(hp, prev_hp=prev_hp, episode_start_hp=episode_start_hp):
        report["died"] = True
        report["cleared"] = False
        report["frames"] = frames
        return True, frames, report

    cleared = (
        not richard_lab_countdown_screen_from_ram(ram)
        and not richard_lab_timer_active(ram)
        and in_control_from_ram(ram)
        and outside_gameplay_reason(ram, episode_start_hp=episode_start_hp) is None
    )
    report["cleared"] = cleared
    report["lab_timer"] = int(ram.get("lab_timer", 0))
    report["frames"] = frames
    return (not cleared), frames, report
