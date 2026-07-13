"""ITEM-screen inventory macros (equip / use / combine).

START opens the ITEM screen from gameplay (hunt 2026-07-07). Cursor homes on
slot 0 each open. Submenu (cross opens) examples:

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
from re1_rl.weapon_equip import read_inventory_ids, weapon_already_equipped

INVENTORY_GRID_COLS = 2
OPEN_START_FRAMES = 12
OPEN_SETTLE_FRAMES = 40
MOVE_TAP_FRAMES = 8
MOVE_SETTLE_FRAMES = 10
SUBMENU_TAP_FRAMES = 15
SUBMENU_SETTLE_FRAMES = 15
EQUIP_SUBMENU_CROSS_FRAMES = 15
EQUIP_SUBMENU_SETTLE_FRAMES = 15
CLOSE_START_FRAMES = 12
CLOSE_ITEM_SETTLE_FRAMES = 30
# Failsafe if RAM cursor never reaches last entry.
COMBINE_CURSOR_MAX_DOWNS = 8

SubmenuEntry = Literal["use", "equip", "combine"]


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


def open_item_screen(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, int]:
    """Open ITEM screen; cursor homes on slot 0. Returns (died, frames, cursor_slot)."""
    frames = 0
    for buttons, n in (({"start": True}, OPEN_START_FRAMES), ({}, OPEN_SETTLE_FRAMES)):
        died, f = _tap(
            client,
            buttons,
            frames=n,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames, 0
    return False, frames, 0


def close_item_screen(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Close ITEM screen opened from gameplay; retry until in-mansion control."""
    from re1_rl.game_session import outside_gameplay_reason

    frames = 0
    for attempt in range(5):
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

        ram = client.read_ram(
            [
                ("game_mode", GAME_MODE, "u8"),
                ("game_state", GAME_STATE, "u32"),
                ("player_hp", PLAYER_HP, "u16"),
            ]
        )
        if int(ram.get("game_mode", 0)) & IN_CONTROL_MASK:
            if outside_gameplay_reason(ram, episode_start_hp=episode_start_hp) is None:
                return False, frames

        if attempt < 4:
            died, f = _tap(
                client,
                {"cross": True},
                frames=8,
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
            frames += f
            if died:
                return True, frames
            died, f = _wait(
                client,
                frames=12,
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
            )
            frames += f
            if died:
                return True, frames
    return False, frames


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


def execute_equip_macro(
    client: Any,
    slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, dict[str, Any]]:
    """Equip the weapon in ``slot`` via ITEM -> EQUIP -> close (gameplay ITEM screen)."""
    target_id = read_inventory_ids(client)[int(slot)] if 0 <= slot < INVENTORY_SLOTS else 0
    before = _read_equipped_id(client)
    if weapon_already_equipped(before, target_id):
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
                "frames": 0,
            },
        )
    frames = 0
    died, f, cursor = open_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot": slot}

    died, f, cursor = _navigate_slot(
        client, cursor, int(slot), prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot": slot}

    died, f = _equip_weapon_submenu(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot": slot}

    died, f = close_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    after = _read_equipped_id(client)
    ram = client.read_ram([("game_mode", GAME_MODE, "u8")])
    in_control = bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK)
    ok = (
        not died
        and target_id != 0
        and after == target_id
        and in_control
    )
    return (
        died,
        frames,
        {
            "ok": ok,
            "reason": "equip_ok" if ok else "equip_failed",
            "slot": int(slot),
            "item_id": target_id,
            "equipped_before": before,
            "equipped_after": after,
            "in_control_after": in_control,
            "frames": frames,
        },
    )


def execute_use_macro(
    client: Any,
    slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, dict[str, Any]]:
    """USE the item in ``slot`` (herbs / sprays)."""
    item_before, qty_before = _read_slot_qty(client, int(slot))
    hp_before = _read_hp(client)
    poison_raw = client.read_ram([("player_poison", PLAYER_POISON, "u8")])
    poisoned = bool(int(poison_raw.get("player_poison", 0)))
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
    frames = 0
    died, f, cursor = open_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot": slot}

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

    died, f = close_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    item_after, qty_after = _read_slot_qty(client, int(slot))
    hp_after = _read_hp(client)
    ram = client.read_ram([("game_mode", GAME_MODE, "u8")])
    in_control = bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK)
    consumed = qty_after < qty_before or item_after == 0
    healed = hp_after > hp_before
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
    died, f, cursor = open_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot_a": slot_a, "slot_b": slot_b}

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
        frames=SUBMENU_SETTLE_FRAMES,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        return True, frames, {"ok": False, "reason": "died", "slot_a": slot_a, "slot_b": slot_b}

    died, f = close_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    inv_after = read_inventory(client)
    ram = client.read_ram([("game_mode", GAME_MODE, "u8")])
    in_control = bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK)
    # Qty-only reloads (empty beretta + bullets) keep the same item ids.
    changed = inv_before != inv_after
    ok = not died and in_control and changed
    return (
        died,
        frames,
        {
            "ok": ok,
            "reason": "combine_ok" if ok else "combine_failed",
            "slot_a": int(slot_a),
            "slot_b": int(slot_b),
            "in_control_after": in_control,
            "frames": frames,
        },
    )
