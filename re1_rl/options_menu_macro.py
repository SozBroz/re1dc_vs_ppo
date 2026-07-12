"""Dismiss in-game OPTIONS / CONFIG menu (bug on some door transitions).

RE1 Director's Cut can open the OPTIONS screen on certain room transitions
(notably Terrace Entry). Training previously treated that as episode failure
+ death penalty. Instead dismiss with DualShock-style inputs (BizHawk names):

  Right, Right, Cross (P1 X)  — select Exit on OPTIONS
  Start                       — close parent pause/ITEM menu Exit returns to

Live validation 2026-07-12 QuickSave1 (``gs=0x80808000``): R-R-X clears
OPTIONS into pause (``0x40808000``); short Start returns to movable gameplay.
"""

from __future__ import annotations

from typing import Any

from re1_rl.game_session import options_menu_from_ram
from re1_rl.memory_map import (
    CHARACTER_ID,
    GAME_MODE,
    GAME_STATE,
    MESSAGE_FLAG,
    PLAYER_HP,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
    player_died,
)
from re1_rl.ram_skip import pause_menu_tree_from_ram

TAP_FRAMES = 8
SETTLE_FRAMES = 20
START_TAP_FRAMES = 4
START_SETTLE_FRAMES = 60
MAX_ATTEMPTS = 3

_RAM_FIELDS = [
    ("player_hp", PLAYER_HP, "u16"),
    ("stage_id", STAGE_ID, "u8"),
    ("room_id", ROOM_ID, "u8"),
    ("character_id", CHARACTER_ID, "u8"),
    ("game_mode", GAME_MODE, "u8"),
    ("game_state", GAME_STATE, "u32"),
    ("msg_flag", MESSAGE_FLAG, "u8"),
    ("scene_flag", SCENE_FLAG, "u8"),
]


def read_options_ram(client: Any) -> dict[str, int]:
    raw = client.read_ram(_RAM_FIELDS)
    return {k: int(v) for k, v in raw.items()}


def still_trapped_in_menu(ram: dict[str, int | float]) -> bool:
    """True while OPTIONS or the parent pause tree still owns the session."""
    return bool(options_menu_from_ram(ram) or pause_menu_tree_from_ram(ram))


def _step(
    client: Any,
    buttons: dict[str, bool],
    *,
    frames: int,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    _, died_flag = client.step(buttons=buttons, n=int(frames))
    if died_flag:
        return True, int(frames)
    hp = int(client.read_ram([("player_hp", PLAYER_HP, "u16")])["player_hp"])
    if player_died(hp, prev_hp=prev_hp, episode_start_hp=episode_start_hp):
        return True, int(frames)
    return False, int(frames)


def _tap_then_settle(
    client: Any,
    buttons: dict[str, bool],
    *,
    prev_hp: int,
    episode_start_hp: int,
    tap_frames: int = TAP_FRAMES,
    settle_frames: int = SETTLE_FRAMES,
) -> tuple[bool, int]:
    total = 0
    died, n = _step(
        client,
        buttons,
        frames=tap_frames,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    total += n
    if died:
        return True, total
    died, n = _step(
        client,
        {},
        frames=settle_frames,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    total += n
    return died, total


def dismiss_options_menu(
    client: Any,
    *,
    prev_hp: int = 0,
    episode_start_hp: int = 0,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[bool, int, dict[str, Any]]:
    """Dismiss OPTIONS (+ parent pause) until clear or attempts exhausted.

    Returns ``(still_trapped, frames_used, report)``.
    ``still_trapped=False`` means the agent can move again.
    """
    frames_used = 0
    report: dict[str, Any] = {
        "attempts": 0,
        "sequence": ["right", "right", "cross", "start"],
        "cleared": False,
    }
    ram0 = read_options_ram(client)
    report["ram_before"] = {
        "game_state": ram0.get("game_state"),
        "game_mode": ram0.get("game_mode"),
        "room_id": ram0.get("room_id"),
    }
    if not options_menu_from_ram(ram0) and not pause_menu_tree_from_ram(ram0):
        report["cleared"] = True
        report["skipped"] = True
        return False, 0, report

    for attempt in range(int(max_attempts)):
        report["attempts"] = attempt + 1
        if options_menu_from_ram(read_options_ram(client)):
            for btn_name in ("right", "right", "cross"):
                died, n = _tap_then_settle(
                    client,
                    {btn_name: True},
                    prev_hp=prev_hp,
                    episode_start_hp=episode_start_hp,
                )
                frames_used += n
                if died:
                    report["died"] = True
                    report["ram_after"] = read_options_ram(client)
                    return True, frames_used, report

        ram = read_options_ram(client)
        if pause_menu_tree_from_ram(ram):
            died, n = _tap_then_settle(
                client,
                {"start": True},
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
                tap_frames=START_TAP_FRAMES,
                settle_frames=START_SETTLE_FRAMES,
            )
            frames_used += n
            if died:
                report["died"] = True
                report["ram_after"] = read_options_ram(client)
                return True, frames_used, report

        ram = read_options_ram(client)
        if not still_trapped_in_menu(ram):
            report["cleared"] = True
            report["ram_after"] = {
                "game_state": ram.get("game_state"),
                "game_mode": ram.get("game_mode"),
                "room_id": ram.get("room_id"),
            }
            return False, frames_used, report

    ram = read_options_ram(client)
    report["ram_after"] = {
        "game_state": ram.get("game_state"),
        "game_mode": ram.get("game_mode"),
        "room_id": ram.get("room_id"),
    }
    trapped = still_trapped_in_menu(ram)
    report["cleared"] = not trapped
    return trapped, frames_used, report
