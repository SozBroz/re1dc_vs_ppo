"""Dismiss in-game OPTIONS / CONFIG menu (bug on some door transitions).

RE1 Director's Cut can open the OPTIONS screen on certain room transitions
(notably Terrace Entry). Training treats lingering menu traps as episode failure
unless we recover. Live hunt on QuickSave8 (2026-07-14):

  - OPTIONS ``gs=0x80808000`` ignores directional cursor nudges.
  - **Start** backs out to the parent pause menu ``0x40808000``.
  - From pause: normalize cursor with **Up**, select **CONTINUE** or **EXIT**
    with **Cross**.
  - Legacy ``gs=0x00000080`` pause/options trap must not skip dismiss (was a
    false ``cleared`` before 2026-07-14).
"""

from __future__ import annotations

from typing import Any

from re1_rl.game_session import (
    options_menu_from_ram,
    pause_or_options_menu_from_ram,
)
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
START_TAP_FRAMES = 12
START_SETTLE_FRAMES = 24
PAUSE_MENU_NORMALIZE_UPS = 20
PAUSE_DISMISS_ROW_ORDER = (0, 5, 4, 1, 2, 3)
MAX_ATTEMPTS = 5

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


def still_trapped_in_menu(
    ram: dict[str, int | float],
    *,
    episode_start_hp: int = 0,
) -> bool:
    """True while OPTIONS, pause tree, or legacy pause/options owns the session."""
    if options_menu_from_ram(ram) or pause_menu_tree_from_ram(ram):
        return True
    if int(episode_start_hp) > 0 and pause_or_options_menu_from_ram(ram):
        return True
    return False


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


def _recovered_from_trap(
    ram: dict[str, int | float],
    *,
    episode_start_hp: int,
) -> bool:
    """Menu trap cleared in RAM (movement optional — wall wedge is ok)."""
    return not still_trapped_in_menu(ram, episode_start_hp=episode_start_hp)


def _back_out_of_options(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """OPTIONS subtree -> parent pause menu via Start."""
    died, frames = _tap_then_settle(
        client,
        {"start": True},
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        tap_frames=START_TAP_FRAMES,
        settle_frames=START_SETTLE_FRAMES,
    )
    return died, frames


def _try_legacy_options_rrx(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """QuickSave1 path: R-R-X clears OPTIONS, then Start closes pause."""
    frames_used = 0
    if not options_menu_from_ram(read_options_ram(client)):
        return False, 0
    for btn_name in ("right", "right", "cross"):
        died, n = _tap_then_settle(
            client,
            {btn_name: True},
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames_used += n
        if died:
            return False, frames_used
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
            return False, frames_used
    ram = read_options_ram(client)
    if _recovered_from_trap(ram, episode_start_hp=episode_start_hp):
        return True, frames_used
    return False, frames_used


def _open_pause_from_trap(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Start from OPTIONS or legacy pause/options — lands on pause tree."""
    frames_used = 0
    ram = read_options_ram(client)
    if options_menu_from_ram(ram):
        died, n = _back_out_of_options(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames_used += n
        return died, frames_used
    if pause_or_options_menu_from_ram(ram) and not pause_menu_tree_from_ram(ram):
        died, n = _tap_then_settle(
            client,
            {"start": True},
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            tap_frames=START_TAP_FRAMES,
            settle_frames=START_SETTLE_FRAMES,
        )
        frames_used += n
        return died, frames_used
    return False, frames_used


def _dismiss_pause_menu(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, int | None]:
    """Try pause-menu rows until gameplay returns."""
    frames_used = 0
    for row in PAUSE_DISMISS_ROW_ORDER:
        for _ in range(PAUSE_MENU_NORMALIZE_UPS):
            died, n = _tap_then_settle(
                client,
                {"up": True},
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
            frames_used += n
            if died:
                return False, frames_used, None

        for _ in range(int(row)):
            died, n = _tap_then_settle(
                client,
                {"down": True},
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
            frames_used += n
            if died:
                return False, frames_used, None

        died, n = _tap_then_settle(
            client,
            {"cross": True},
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            tap_frames=START_TAP_FRAMES,
            settle_frames=START_SETTLE_FRAMES,
        )
        frames_used += n
        if died:
            return False, frames_used, None

        died, n = _step(
            client,
            {},
            frames=30,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames_used += n
        if died:
            return False, frames_used, None

        ram = read_options_ram(client)
        if options_menu_from_ram(ram):
            died, n = _back_out_of_options(
                client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
            )
            frames_used += n
            if died:
                return False, frames_used, None
            continue

        if _recovered_from_trap(ram, episode_start_hp=episode_start_hp):
            return True, frames_used, row

    return False, frames_used, None


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
        "sequence": ["start", "up", "down", "cross", "rrx"],
        "cleared": False,
    }
    ram0 = read_options_ram(client)
    report["ram_before"] = {
        "game_state": ram0.get("game_state"),
        "game_mode": ram0.get("game_mode"),
        "room_id": ram0.get("room_id"),
    }
    if not still_trapped_in_menu(ram0, episode_start_hp=episode_start_hp):
        report["cleared"] = True
        report["skipped"] = True
        return False, 0, report

    for attempt in range(int(max_attempts)):
        report["attempts"] = attempt + 1

        ok, n = _try_legacy_options_rrx(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames_used += n
        if ok:
            report["cleared"] = True
            report["path"] = "legacy_rrx"
            report["ram_after"] = read_options_ram(client)
            return False, frames_used, report

        died, n = _open_pause_from_trap(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames_used += n
        if died:
            report["died"] = True
            report["ram_after"] = read_options_ram(client)
            return True, frames_used, report

        ram = read_options_ram(client)
        if pause_menu_tree_from_ram(ram) or still_trapped_in_menu(
            ram, episode_start_hp=episode_start_hp
        ):
            cleared, n, row = _dismiss_pause_menu(
                client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
            )
            frames_used += n
            if row is not None:
                report["exit_row"] = row
            if cleared:
                report["cleared"] = True
                report["path"] = "pause_rows"
                report["ram_after"] = read_options_ram(client)
                return False, frames_used, report

        ram = read_options_ram(client)
        if _recovered_from_trap(ram, episode_start_hp=episode_start_hp):
            report["cleared"] = True
            report["path"] = "implicit_clear"
            report["ram_after"] = read_options_ram(client)
            return False, frames_used, report

        died, n = _tap_then_settle(
            client,
            {"circle": True},
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames_used += n
        if died:
            report["died"] = True
            report["ram_after"] = read_options_ram(client)
            return True, frames_used, report

    ram = read_options_ram(client)
    report["ram_after"] = {
        "game_state": ram.get("game_state"),
        "game_mode": ram.get("game_mode"),
        "room_id": ram.get("room_id"),
    }
    trapped = still_trapped_in_menu(ram, episode_start_hp=episode_start_hp)
    report["cleared"] = not trapped
    return trapped, frames_used, report
