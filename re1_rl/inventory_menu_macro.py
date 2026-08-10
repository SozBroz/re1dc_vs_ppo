"""ITEM-screen inventory macros (equip / use / combine).

START opens the ITEM screen from gameplay (hunt 2026-07-07). Fresh opens home
on inventory slot 0 (live DC); do not seed from the equipped weapon or Up can
land on EXIT. Orphan already-open menus are closed and reopened for a known
cursor. Submenu (cross opens) examples:

  Weapons: EQUIP → CHECK → COMBN
  Spray / ammo: USE → CHECK → COMBN

COMBN is selected by reading ``ITEM_SUBMENU_CURSOR`` / ``ITEM_SUBMENU_N_ENTRIES``
and tapping ``down`` until the cursor is on the last entry (live hunt 2026-07-12),
not by a hardcoded down count.

Only item-box deposit/withdraw may RAM-cheat; these macros drive real buttons.

Navigation grid (Jill, 8 slots):
  0  1
  2  3
  4  5
  6  7
"""

from __future__ import annotations

from typing import Any, Literal

from re1_rl.memory_map import (
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    GAME_STATE,
    INVENTORY_BASE,
    INVENTORY_SLOTS,
    IN_CONTROL_MASK,
    ITEM_SUBMENU_CURSOR,
    ITEM_SUBMENU_N_ENTRIES,
    PLAYER_HP,
    PLAYER_POISON,
    player_died,
)
from re1_rl.item_use import use_would_help
from re1_rl.weapon_equip import (
    read_equipped_slot_0based,
    read_inventory_ids,
    weapon_already_equipped,
)

INVENTORY_GRID_COLS = 2
OPEN_START_FRAMES = 12
OPEN_SETTLE_FRAMES = 40
# After Start+settle, poll — ITEM gs sometimes lands late; also used between
# toggle-recovery attempts when an undetected open menu made Start *close* it.
OPEN_CONFIRM_POLL_FRAMES = 2
OPEN_CONFIRM_MAX_EXTRA = 24
OPEN_START_ATTEMPTS = 2
MOVE_TAP_FRAMES = 8
MOVE_SETTLE_FRAMES = 10
SUBMENU_TAP_FRAMES = 15
SUBMENU_SETTLE_FRAMES = 15
EQUIP_SUBMENU_CROSS_FRAMES = 15
EQUIP_SUBMENU_SETTLE_FRAMES = 15
CLOSE_START_FRAMES = 12
CLOSE_ITEM_SETTLE_FRAMES = 30
# Document/file examine (QS1 botany book): Triangle hold matches knife_equip close.
CLOSE_TRIANGLE_FRAMES = 15
CLOSE_TRIANGLE_SETTLE_FRAMES = 20
CLOSE_TRIANGLE_MAX_ATTEMPTS = 12
# Post-COMBINE reload often leaves the ITEM grid up where Triangle alone does
# not exit (fleet: item_menu_dismiss_fail frames=420). Prefer Triangle, then
# one Start toggle, then Triangle again — never Start-only looping.
CLOSE_TRIANGLE_PRIMARY_ATTEMPTS = 4
CLOSE_START_FALLBACK_ATTEMPTS = 1
CLOSE_TRIANGLE_AFTER_START_ATTEMPTS = 4
# Story key-item USE: poll slowly after submenu USE; do not close ITEM immediately.
STORY_USE_POLL_FRAMES = 4
STORY_USE_MENU_STALL_FRAMES = 120
STORY_USE_MAX_WAIT_FRAMES = 900
# Failsafe if RAM cursor never reaches last entry.
COMBINE_CURSOR_MAX_DOWNS = 8

SubmenuEntry = Literal["use", "equip", "combine"]


def _item_menu_confirmed(client: Any) -> bool:
    """True when RAM shows the START/ITEM pause tree (not OPTIONS)."""
    from re1_rl.ram_skip import item_inventory_screen_from_ram

    ram = client.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
        ]
    )
    return item_inventory_screen_from_ram(ram)


def read_item_submenu_cursor(client: Any) -> tuple[int, int]:
    """Return ``(cursor_index, n_entries)`` for the open ITEM action submenu."""
    ram = client.read_ram(
        [
            ("submenu_cursor", ITEM_SUBMENU_CURSOR, "u8"),
            ("submenu_n_entries", ITEM_SUBMENU_N_ENTRIES, "u8"),
        ]
    )
    return int(ram.get("submenu_cursor", 0)), int(ram.get("submenu_n_entries", 0))


def combine_submenu_target_index(n_entries: int) -> int:
    """COMBN is the last submenu entry (weapon / spray / ammo live check)."""
    n = int(n_entries)
    if n <= 0:
        return 0
    return n - 1


def slot_nav_moves(from_slot: int, to_slot: int) -> list[str]:
    """Relative d-pad moves between inventory grid slots (safe from slot 0 open)."""
    if from_slot < 0 or to_slot < 0:
        raise ValueError("inventory slots must be non-negative")
    fr, fc = divmod(int(from_slot), INVENTORY_GRID_COLS)
    tr, tc = divmod(int(to_slot), INVENTORY_GRID_COLS)
    moves: list[str] = []
    dr = tr - fr
    if dr > 0:
        moves.extend(["down"] * dr)
    elif dr < 0:
        if fr + dr < 0:
            raise ValueError(f"cannot navigate up from row {fr} without hitting header")
        moves.extend(["up"] * (-dr))
    dc = tc - fc
    if dc > 0:
        moves.extend(["right"] * dc)
    elif dc < 0:
        moves.extend(["left"] * (-dc))
    return moves


def _read_hp(client: Any) -> int:
    raw = client.read_ram([("player_hp", PLAYER_HP, "u16")])
    return int(raw["player_hp"])


def _step_batch(
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
    hp = _read_hp(client)
    if player_died(hp, prev_hp=prev_hp, episode_start_hp=episode_start_hp):
        return True, int(frames)
    return False, int(frames)


def _tap(
    client: Any,
    buttons: dict[str, bool],
    *,
    frames: int,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    return _step_batch(
        client,
        buttons,
        frames=frames,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )


def _wait(
    client: Any,
    *,
    frames: int,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    return _step_batch(
        client,
        {},
        frames=frames,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )


def _read_menu_ram(client: Any) -> dict[str, int]:
    ram = client.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
        ]
    )
    return {
        "game_mode": int(ram.get("game_mode", 0)),
        "game_state": int(ram.get("game_state", 0)),
    }


def _poll_item_menu_open(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
    max_extra: int = OPEN_CONFIRM_MAX_EXTRA,
) -> tuple[bool, int, bool]:
    """Poll for ITEM pause tree. Returns ``(died, frames, opened)``."""
    frames = 0
    if _item_menu_confirmed(client):
        return False, frames, True
    polls = max(0, int(max_extra) // int(OPEN_CONFIRM_POLL_FRAMES))
    for _ in range(polls):
        died, f = _wait(
            client,
            frames=OPEN_CONFIRM_POLL_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames, False
        if _item_menu_confirmed(client):
            return False, frames, True
    return False, frames, False


def open_item_screen(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, int, bool]:
    """Start-tap to open ITEM; proceed only if RAM confirms the menu.

    Returns ``(died, frames, cursor_slot, opened)``. Cursor is always ``0`` on a
    successful open — live DC opens home on inventory slot 0 (not the equipped
    weapon).

    If ITEM is already open (orphan pause), close and reopen so the cursor is
    known. After Start, poll for late gs, and if still closed try a second Start
    (toggle recovery: an undetected open menu makes the first Start *close* it,
    which used to abort as ``item_menu_open_failed`` before any slot navigation —
    looked like “stuck on knife, never moved to the gun”).
    """
    from re1_rl.ram_skip import options_menu_from_ram

    frames = 0
    if _item_menu_confirmed(client):
        died, f = close_item_screen(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            return True, frames, 0, False
        if _item_menu_confirmed(client):
            # Still stuck in pause — do not tap Start again.
            return False, frames, 0, False

    for attempt in range(int(OPEN_START_ATTEMPTS)):
        for buttons, n in (
            ({"start": True}, OPEN_START_FRAMES),
            ({}, OPEN_SETTLE_FRAMES),
        ):
            died, f = _tap(
                client,
                buttons,
                frames=n,
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
            frames += f
            if died:
                return True, frames, 0, False
        died, f, opened = _poll_item_menu_open(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            return True, frames, 0, False
        if opened:
            return False, frames, 0, True

        menu_ram = _read_menu_ram(client)
        if options_menu_from_ram(menu_ram):
            from re1_rl.options_menu_macro import dismiss_options_menu

            _still, f, _rep = dismiss_options_menu(
                client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
            )
            frames += f

    return False, frames, 0, False


def _item_menu_cleared_ok(
    client: Any,
    *,
    episode_start_hp: int,
) -> bool:
    """True when ITEM/STATUS pause is gone (and gameplay if in-control bit set)."""
    from re1_rl.game_session import outside_gameplay_reason

    if _item_menu_confirmed(client):
        return False
    ram = client.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
            ("player_hp", PLAYER_HP, "u16"),
        ]
    )
    if int(ram.get("game_mode", 0)) & IN_CONTROL_MASK:
        return outside_gameplay_reason(ram, episode_start_hp=episode_start_hp) is None
    return True


def close_item_screen(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Close ITEM/status: Triangle cancel, then guarded Start fallback.

    RE1 DC manual: Start opens/toggles the status screen; Triangle cancels.
    Triangle-only close (post-equip) avoids Start reopen flash, but after
    ammo COMBINE/reload Triangle often never clears the pause tree — agents
    stay cursor-on-ammo until softlock. Fall back to one Start toggle, then
    Triangle again if Start left/reopened the menu.
    """
    frames = 0

    def _triangle_pass(attempts: int) -> tuple[bool, bool]:
        """Returns ``(died, cleared)``."""
        nonlocal frames
        for _ in range(int(attempts)):
            if _item_menu_cleared_ok(client, episode_start_hp=episode_start_hp):
                return False, True
            died, f = _tap(
                client,
                {"triangle": True},
                frames=CLOSE_TRIANGLE_FRAMES,
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
            frames += f
            if died:
                return True, False
            died, f = _wait(
                client,
                frames=CLOSE_TRIANGLE_SETTLE_FRAMES,
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
            frames += f
            if died:
                return True, False
            if _item_menu_cleared_ok(client, episode_start_hp=episode_start_hp):
                return False, True
        return False, _item_menu_cleared_ok(client, episode_start_hp=episode_start_hp)

    died, cleared = _triangle_pass(CLOSE_TRIANGLE_PRIMARY_ATTEMPTS)
    if died or cleared:
        return died, frames

    for _ in range(int(CLOSE_START_FALLBACK_ATTEMPTS)):
        died, f = _tap(
            client,
            {"start": True},
            frames=CLOSE_START_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames
        died, f = _wait(
            client,
            frames=CLOSE_ITEM_SETTLE_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames
        if _item_menu_cleared_ok(client, episode_start_hp=episode_start_hp):
            return False, frames

    died, cleared = _triangle_pass(CLOSE_TRIANGLE_AFTER_START_ATTEMPTS)
    return died, frames


def close_document_examine_ui(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Triangle-dismiss document/file examine UI (QS1 botany book).

    Exact pause sub-state ``mode=0x40``, ``gs=0x40808100`` (not ITEM
    ``0x40808000``). Start does not exit this overlay — Triangle does.
    Returns ``(died, frames_used)``.
    """
    frames = 0
    for _ in range(CLOSE_TRIANGLE_MAX_ATTEMPTS):
        if not _item_menu_confirmed(client):
            return False, frames
        died, f = _tap(
            client,
            {"triangle": True},
            frames=CLOSE_TRIANGLE_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames
        died, f = _wait(
            client,
            frames=CLOSE_TRIANGLE_SETTLE_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames
    return False, frames


def dismiss_orphan_item_menu(
    client: Any,
    *,
    prev_hp: int = 0,
    episode_start_hp: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Close an orphan START/ITEM pause or document-examine overlay.

    Document examine (``gs=0x40808100``) goes straight to Triangle. Normal
    ITEM/STATUS/MAP tries Start first, then Triangle as a fallback.

    Returns ``(still_open, frames_used, report)``. ``still_open=False`` means
    Jill is back in mansion control (or was never on the pause tree).
    """
    from re1_rl.ram_skip import document_examine_ui_from_ram

    report: dict[str, Any] = {"cleared": False, "path": "triangle_cancel"}
    if not _item_menu_confirmed(client):
        report["cleared"] = True
        report["skipped"] = True
        return False, 0, report

    ram = client.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
        ]
    )
    if document_examine_ui_from_ram(ram):
        died, frames = close_document_examine_ui(
            client,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        still = _item_menu_confirmed(client)
        report["path"] = "triangle_document"
        report["cleared"] = not still
        report["died"] = bool(died)
        report["frames"] = int(frames)
        return still, frames, report

    # Normal ITEM/STATUS: Triangle first, Start fallback inside close_item_screen.
    died, frames = close_item_screen(
        client,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    still = _item_menu_confirmed(client)
    report["cleared"] = not still
    report["died"] = bool(died)
    report["frames"] = int(frames)
    if not still:
        report["path"] = "triangle_or_start_cancel"
    return still, frames, report


def _wait_for_story_use_after_pick(
    client: Any,
    *,
    slot: int,
    story_site: dict[str, Any],
    inv_before: list[tuple[int, int]],
    probe_before: dict[str, Any],
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, bool]:
    """Poll RAM after ITEM->USE; let cutscene start before closing menu."""
    from re1_rl.item_box import read_inventory
    from re1_rl.ram_skip import item_inventory_screen_from_ram
    from re1_rl.story_item_use import read_story_use_probe, story_use_macro_resolved

    frames = 0
    menu_stall = 0
    resolved = False

    while frames < STORY_USE_MAX_WAIT_FRAMES:
        probe_now = read_story_use_probe(client)
        inv_now = read_inventory(client)
        if story_use_macro_resolved(
            before=probe_before,
            after=probe_now,
            site=story_site,
            slot=int(slot),
            inventory_before=inv_before,
            inventory_after=inv_now,
        ):
            resolved = True
            break

        ram = {
            "game_mode": probe_now.get("game_mode", 0),
            "game_state": probe_now.get("game_state", 0),
            "scene_flag": probe_now.get("scene_flag", 0),
            "msg_flag": probe_now.get("msg_flag", 0),
        }
        if item_inventory_screen_from_ram(ram):
            menu_stall += STORY_USE_POLL_FRAMES
            if menu_stall >= STORY_USE_MENU_STALL_FRAMES:
                break
        else:
            menu_stall = 0

        died, f = _wait(
            client,
            frames=STORY_USE_POLL_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames, False

    probe_tail = read_story_use_probe(client)
    ram_tail = {
        "game_state": probe_tail.get("game_state", 0),
        "game_mode": probe_tail.get("game_mode", 0),
        "scene_flag": probe_tail.get("scene_flag", 0),
        "msg_flag": probe_tail.get("msg_flag", 0),
    }
    if item_inventory_screen_from_ram(ram_tail):
        died, f = close_item_screen(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            return True, frames, resolved

    return False, frames, resolved


def _navigate_slot(
    client: Any,
    cursor_slot: int,
    target_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, int]:
    frames = 0
    try:
        moves = slot_nav_moves(cursor_slot, target_slot)
    except ValueError:
        return False, frames, cursor_slot
    for move in moves:
        died, f = _tap(
            client,
            {move: True},
            frames=MOVE_TAP_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames, cursor_slot
        died, f = _wait(
            client,
            frames=MOVE_SETTLE_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames, cursor_slot
    return False, frames, int(target_slot)


def _pick_submenu_entry(
    client: Any,
    entry: SubmenuEntry,
    *,
    prev_hp: int,
    episode_start_hp: int,
    combine_downs: int | None = None,
) -> tuple[bool, int]:
    """Open ITEM submenu and confirm ``entry``.

    For ``combine``, prefer live RAM: read submenu cursor / entry count and
    tap ``down`` until the last entry (COMBN) is highlighted. Pass
    ``combine_downs`` to force a fixed down count (tests / offline fakes).
    """
    frames = 0
    died, f = _tap(
        client,
        {"cross": True},
        frames=SUBMENU_TAP_FRAMES,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        return True, frames
    died, f = _wait(
        client,
        frames=SUBMENU_SETTLE_FRAMES,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        return True, frames

    if entry == "combine":
        if combine_downs is not None:
            downs_plan = max(0, int(combine_downs))
            for _ in range(downs_plan):
                died, f = _tap(
                    client,
                    {"down": True},
                    frames=MOVE_TAP_FRAMES,
                    prev_hp=prev_hp,
                    episode_start_hp=episode_start_hp,
                )
                frames += f
                if died:
                    return True, frames
                died, f = _wait(
                    client,
                    frames=MOVE_SETTLE_FRAMES,
                    prev_hp=prev_hp,
                    episode_start_hp=episode_start_hp,
                )
                frames += f
                if died:
                    return True, frames
        else:
            try:
                cursor, n_entries = read_item_submenu_cursor(client)
                target = combine_submenu_target_index(n_entries)
            except (OSError, RuntimeError, AttributeError, TypeError, ValueError, KeyError):
                cursor, target = 0, 2
            downs = 0
            while cursor < target and downs < COMBINE_CURSOR_MAX_DOWNS:
                died, f = _tap(
                    client,
                    {"down": True},
                    frames=MOVE_TAP_FRAMES,
                    prev_hp=prev_hp,
                    episode_start_hp=episode_start_hp,
                )
                frames += f
                if died:
                    return True, frames
                died, f = _wait(
                    client,
                    frames=MOVE_SETTLE_FRAMES,
                    prev_hp=prev_hp,
                    episode_start_hp=episode_start_hp,
                )
                frames += f
                if died:
                    return True, frames
                downs += 1
                try:
                    cursor, _n = read_item_submenu_cursor(client)
                except (OSError, RuntimeError, AttributeError, TypeError, ValueError, KeyError):
                    cursor = downs
        died, f = _tap(
            client,
            {"cross": True},
            frames=SUBMENU_TAP_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames
        died, f = _wait(
            client,
            frames=SUBMENU_SETTLE_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        return died, frames

    # use / equip: confirm top entry
    died, f = _tap(
        client,
        {"cross": True},
        frames=SUBMENU_TAP_FRAMES,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        return True, frames
    died, f = _wait(
        client,
        frames=SUBMENU_SETTLE_FRAMES,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    return died, frames


def _equip_weapon_submenu(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Weapon at cursor: cross opens submenu, cross confirms EQUIP (hunt 2026-07-07)."""
    frames = 0
    for buttons, n in (
        ({"cross": True}, EQUIP_SUBMENU_CROSS_FRAMES),
        ({}, EQUIP_SUBMENU_SETTLE_FRAMES),
        ({"cross": True}, EQUIP_SUBMENU_CROSS_FRAMES),
        ({}, EQUIP_SUBMENU_SETTLE_FRAMES),
    ):
        if buttons:
            died, f = _tap(
                client,
                buttons,
                frames=n,
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
        else:
            died, f = _wait(
                client,
                frames=n,
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
        frames += f
        if died:
            return True, frames
    return False, frames


def _read_equipped_id(client: Any) -> int:
    raw = client.read_ram([("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8")])
    return int(raw["equipped_weapon_id"])


def _read_slot_qty(client: Any, slot: int) -> tuple[int, int]:
    raw = client.read_ram([(f"inv_slot_{slot}", INVENTORY_BASE + 2 * slot, "u16")])
    packed = int(raw[f"inv_slot_{slot}"])
    return packed & 0xFF, packed >> 8


def _equip_ram_snapshot(client: Any) -> dict[str, Any]:
    """Equipped-id + both slot mirrors + menu/control bits for thrash forensics."""
    from re1_rl.memory_map import EQUIPPED_SLOT_INDEX, EQUIPPED_SLOT_INDEX_1BASED

    try:
        ram = client.read_ram(
            [
                ("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8"),
                ("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8"),
                ("equipped_slot_0based", EQUIPPED_SLOT_INDEX, "u8"),
                ("game_mode", GAME_MODE, "u8"),
                ("game_state", GAME_STATE, "u32"),
            ]
        )
    except (OSError, RuntimeError, AttributeError, TypeError, ValueError, KeyError):
        return {}
    slot_1b = int(ram.get("equipped_slot_1based", 0))
    slot_0_mirror = int(ram.get("equipped_slot_0based", 0))
    snap: dict[str, Any] = {
        "eq": int(ram.get("equipped_weapon_id", 0)),
        "slot_1b": slot_1b,
        "slot_0": slot_1b - 1 if slot_1b > 0 else None,
        "slot_0_mirror": slot_0_mirror,
        "gm": int(ram.get("game_mode", 0)),
        "gs": int(ram.get("game_state", 0)),
        "menu": bool(_item_menu_confirmed(client)),
    }
    if snap["slot_0"] is not None and snap["slot_0"] != slot_0_mirror:
        snap["mirror_disagree"] = True
    return snap


def execute_equip_macro(
    client: Any,
    slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, dict[str, Any]]:
    """Equip the weapon in ``slot`` via ITEM -> EQUIP -> close (gameplay ITEM screen)."""
    target_id = read_inventory_ids(client)[int(slot)] if 0 <= slot < INVENTORY_SLOTS else 0
    before_snap = _equip_ram_snapshot(client)
    before = int(before_snap.get("eq", 0)) if before_snap else _read_equipped_id(client)
    before_slot = before_snap.get("slot_0") if before_snap else None
    if before_slot is None:
        try:
            before_slot = read_equipped_slot_0based(client)
        except (OSError, RuntimeError, AttributeError, TypeError, ValueError, KeyError):
            before_slot = None
    stages: list[dict[str, Any]] = [
        {
            "t": "pre",
            "f": 0,
            "target_slot": int(slot),
            "target_id": int(target_id),
            **before_snap,
        }
    ]
    if weapon_already_equipped(
        before,
        target_id,
        equipped_slot_0based=before_slot,
        slot=int(slot),
    ):
        return (
            False,
            0,
            {
                "ok": True,
                "reason": "already_equipped",
                "slot": int(slot),
                "item_id": target_id,
                "equipped_before": before,
                "equipped_after": before,
                "equipped_slot_before": before_slot,
                "equipped_slot_after": before_slot,
                "frames": 0,
                "stages": stages,
                "anomaly": "redundant_equip_short_circuit",
            },
        )
    frames = 0
    died, f, cursor, opened = open_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    open_snap = _equip_ram_snapshot(client)
    stages.append(
        {
            "t": "opened" if opened else "open_failed",
            "f": frames,
            "cursor_home": 0,
            "opened": bool(opened),
            **open_snap,
        }
    )
    if died:
        return True, frames, {
            "ok": False,
            "reason": "died",
            "slot": slot,
            "stages": stages,
        }
    if not opened:
        menu_ram = _read_menu_ram(client)
        return (
            False,
            frames,
            {
                "ok": False,
                "reason": "item_menu_open_failed",
                "slot": int(slot),
                "item_id": target_id,
                "equipped_before": before,
                "equipped_slot_before": before_slot,
                "frames": frames,
                "game_mode": menu_ram["game_mode"],
                "game_state": menu_ram["game_state"],
                "stages": stages,
            },
        )

    anomaly: str | None = None
    # Belt-and-suspenders: target already held should have short-circuited above.
    if int(target_id) == int(before) and int(target_id) != 0:
        anomaly = "redundant_equip_opened"

    died, f, cursor = _navigate_slot(
        client, cursor, int(slot), prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    nav_snap = _equip_ram_snapshot(client)
    stages.append(
        {
            "t": "navigated",
            "f": frames,
            "cursor": int(cursor),
            "target_slot": int(slot),
            **nav_snap,
        }
    )
    if died:
        return True, frames, {
            "ok": False,
            "reason": "died",
            "slot": slot,
            "stages": stages,
        }

    died, f = _equip_weapon_submenu(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    conf_snap = _equip_ram_snapshot(client)
    stages.append({"t": "confirmed", "f": frames, **conf_snap})
    if died:
        return True, frames, {
            "ok": False,
            "reason": "died",
            "slot": slot,
            "stages": stages,
        }

    died, f = close_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    after_snap = _equip_ram_snapshot(client)
    after = int(after_snap.get("eq", 0)) if after_snap else _read_equipped_id(client)
    after_slot = after_snap.get("slot_0")
    stages.append({"t": "closed", "f": frames, **after_snap})
    in_control = bool(int(after_snap.get("gm", 0)) & IN_CONTROL_MASK) if after_snap else False
    if after_snap.get("menu"):
        anomaly = anomaly or "equip_menu_left_open"
    if after != target_id:
        anomaly = anomaly or "equip_target_mismatch"
    if after_snap.get("mirror_disagree") or before_snap.get("mirror_disagree"):
        anomaly = anomaly or "equip_mirror_disagree"
    # Knife-on-knife lookalike: targeted knife while RAM already knife (should be impossible).
    if int(target_id) == 1 and int(before) == 1:
        anomaly = "knife_on_knife_dispatched"
    ok = (
        not died
        and target_id != 0
        and after == target_id
        and in_control
    )
    report: dict[str, Any] = {
        "ok": ok,
        "reason": "equip_ok" if ok else "equip_failed",
        "slot": int(slot),
        "item_id": target_id,
        "equipped_before": before,
        "equipped_after": after,
        "equipped_slot_before": before_slot,
        "equipped_slot_after": after_slot,
        "in_control_after": in_control,
        "frames": frames,
        "stages": stages,
        "zero_nav": int(slot) == 0,
    }
    if anomaly:
        report["anomaly"] = anomaly
    return (died, frames, report)


def execute_use_macro(
    client: Any,
    slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
    story_site: dict[str, Any] | None = None,
) -> tuple[bool, int, dict[str, Any]]:
    """USE the item in ``slot`` (herbs / sprays, or story key at a use site)."""
    from re1_rl.item_box import read_inventory
    from re1_rl.story_item_use import read_story_use_probe, story_use_macro_resolved

    item_before, qty_before = _read_slot_qty(client, int(slot))
    hp_before = _read_hp(client)
    from re1_rl.memory_map import player_poisoned_from_raw

    poison_raw = client.read_ram([("player_poison", PLAYER_POISON, "u8")])
    poisoned = player_poisoned_from_raw(poison_raw.get("player_poison", 0))
    story = story_site is not None
    if not story:
        if not use_would_help(
            int(item_before),
            current_hp=hp_before,
            poisoned=poisoned,
            episode_start_hp=episode_start_hp,
        ):
            return (
                False,
                0,
                {
                    "ok": False,
                    "reason": "use_would_not_help",
                    "slot": int(slot),
                    "item_id": int(item_before),
                    "hp_before": hp_before,
                    "frames": 0,
                },
            )
    elif int(item_before) == 0 or (not story and int(qty_before) <= 0):
        return (
            False,
            0,
            {
                "ok": False,
                "reason": "story_slot_empty" if story else "use_would_not_help",
                "slot": int(slot),
                "item_id": int(item_before),
                "frames": 0,
            },
        )
    inv_before = read_inventory(client)
    probe_before = read_story_use_probe(client)
    frames = 0
    died, f, cursor, opened = open_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot": slot}
    if not opened:
        return (
            False,
            frames,
            {
                "ok": False,
                "reason": "item_menu_open_failed",
                "slot": int(slot),
                "item_id": int(item_before),
                "frames": frames,
            },
        )

    died, f, cursor = _navigate_slot(
        client, cursor, int(slot), prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot": slot}

    died, f = _pick_submenu_entry(
        client, "use", prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot": slot}

    if story and story_site is not None:
        died, f, resolved = _wait_for_story_use_after_pick(
            client,
            slot=int(slot),
            story_site=story_site,
            inv_before=inv_before,
            probe_before=probe_before,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames, {"ok": False, "reason": "died", "slot": slot}
    else:
        died, f = close_item_screen(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            return True, frames, {"ok": False, "reason": "died", "slot": slot}
        resolved = False

    item_after, qty_after = _read_slot_qty(client, int(slot))
    hp_after = _read_hp(client)
    ram = client.read_ram([("game_mode", GAME_MODE, "u8")])
    in_control = bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK)
    consumed = qty_after < qty_before or item_after == 0
    healed = hp_after > hp_before
    if story and story_site is not None:
        inv_after = read_inventory(client)
        probe_after = read_story_use_probe(client)
        ok = not died and resolved and story_use_macro_resolved(
            before=probe_before,
            after=probe_after,
            site=story_site,
            slot=int(slot),
            inventory_before=inv_before,
            inventory_after=inv_after,
        )
        return (
            died,
            frames,
            {
                "ok": ok,
                "reason": "story_use_ok" if ok else "story_use_failed",
                "slot": int(slot),
                "item_id": item_before,
                "story_use_site": str(story_site.get("id", "")),
                "story_use_item": str(story_site.get("item", "")),
                "in_control_after": in_control,
                "frames": frames,
            },
        )
    ok = not died and in_control and (consumed or healed)
    return (
        died,
        frames,
        {
            "ok": ok,
            "reason": "use_ok" if ok else "use_failed",
            "slot": int(slot),
            "item_id": item_before,
            "heal_applied": max(0, hp_after - hp_before),
            "in_control_after": in_control,
            "frames": frames,
        },
    )


def execute_combine_macro(
    client: Any,
    slot_a: int,
    slot_b: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, dict[str, Any]]:
    """COMBINE items in ``slot_a`` then ``slot_b`` (ordered)."""
    from re1_rl.item_box import read_inventory

    inv_before = read_inventory(client)
    frames = 0
    died, f, cursor, opened = open_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot_a": slot_a, "slot_b": slot_b}
    if not opened:
        return (
            False,
            frames,
            {
                "ok": False,
                "reason": "item_menu_open_failed",
                "slot_a": int(slot_a),
                "slot_b": int(slot_b),
                "frames": frames,
            },
        )

    died, f, cursor = _navigate_slot(
        client, cursor, int(slot_a), prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot_a": slot_a, "slot_b": slot_b}

    died, f = _pick_submenu_entry(
        client,
        "combine",
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot_a": slot_a, "slot_b": slot_b}

    died, f, cursor = _navigate_slot(
        client, cursor, int(slot_b), prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot_a": slot_a, "slot_b": slot_b}

    # After partner slot is highlighted: Cross selects it, then one or two more
    # Cross taps confirm the merge prompt (live cp43 herb hunt 2026-08-10 —
    # first confirm alone often no-ops; second confirm applies MIXED_GG).
    for confirm_i in range(3):
        died, f = _tap(
            client,
            {"cross": True},
            frames=SUBMENU_TAP_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames, {"ok": False, "reason": "died", "slot_a": slot_a, "slot_b": slot_b}
        died, f = _wait(
            client,
            frames=max(SUBMENU_SETTLE_FRAMES, 30),
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames, {"ok": False, "reason": "died", "slot_a": slot_a, "slot_b": slot_b}
        if read_inventory(client) != inv_before:
            break

    died, f = close_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    dismiss_report: dict[str, Any] | None = None
    if not died and _item_menu_confirmed(client):
        still_open, f, dismiss_report = dismiss_orphan_item_menu(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if still_open and not dismiss_report.get("died"):
            # Last resort already inside dismiss; keep report for telemetry.
            pass
        died = bool(died or dismiss_report.get("died"))
    inv_after = read_inventory(client)
    ram = client.read_ram([("game_mode", GAME_MODE, "u8")])
    in_control = bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK)
    menu_open = _item_menu_confirmed(client)
    # Qty-only reloads (empty beretta + bullets) keep the same item ids.
    changed = inv_before != inv_after
    ok = not died and in_control and changed and not menu_open
    report: dict[str, Any] = {
        "ok": ok,
        "reason": "combine_ok" if ok else "combine_failed",
        "slot_a": int(slot_a),
        "slot_b": int(slot_b),
        "in_control_after": in_control,
        "menu_open_after": menu_open,
        "inventory_changed": changed,
        "frames": frames,
    }
    if dismiss_report is not None:
        report["menu_dismiss"] = dismiss_report
    return died, frames, report
