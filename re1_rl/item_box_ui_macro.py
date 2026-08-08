"""Authentic item-box UI macros (no magic RAM writes).

RE1 DC box flow (imperator scaffolding 2026-08-07):
  - Interact opens the box; after the animation the cursor is on inventory
    slot 0 (top-left).
  - Withdraw: move to an **empty** inventory slot → Cross (cursor enters the
    box list on slot 0) → Up/Down to the source box slot → Cross. The game
    places the item in the chosen empty slot and leaves the cursor on it.
  - Next withdraw: from that cursor, move to the next empty inv slot → …
  - Close: from the inventory cursor, Down to the EXIT button → Cross.
    Triangle remains a fallback dismiss.

Deposit stays available for later; policy currently off.
"""

from __future__ import annotations

from typing import Any

from re1_rl.inventory_menu_macro import slot_nav_moves
from re1_rl.item_box import (
    BOX_SLOTS,
    INVENTORY_SLOTS,
    can_deposit,
    can_withdraw,
    read_box,
    read_inventory,
)
from re1_rl.memory_map import GAME_MODE, GAME_STATE, PLAYER_HP, player_died

OPEN_PANE_TAP_FRAMES = 8
OPEN_PANE_SETTLE_FRAMES = 16
# Box-screen d-pad needs a longer settle than START/ITEM (live QA 2026-08-07).
BOX_MOVE_TAP_FRAMES = 8
BOX_MOVE_SETTLE_FRAMES = 20
TRANSFER_TAP_FRAMES = 8
TRANSFER_SETTLE_FRAMES = 55
CLOSE_TRIANGLE_FRAMES = 12
CLOSE_SETTLE_FRAMES = 40
CLOSE_MAX_ATTEMPTS = 4
EXIT_NAV_MAX_DOWNS = 6
# Open animation keeps mode/gs in the pause tree before the grid accepts d-pad.
POST_OPEN_SETTLE_FRAMES = 90


def _tap(
    client: Any,
    buttons: dict[str, bool],
    *,
    frames: int,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Advance frames. Box UI can flicker HP reads — ignore step death flag."""
    used = 0
    n = max(0, int(frames))
    if n <= 0:
        return False, 0
    client.step(buttons=buttons, n=n, abort_on_zero_hp=False)
    used += n
    try:
        ram = client.read_ram([("player_hp", PLAYER_HP, "u16")])
        hp = int(ram.get("player_hp", 0))
        if player_died(hp, prev_hp=prev_hp, episode_start_hp=episode_start_hp):
            return True, used
    except (OSError, RuntimeError, AttributeError, TypeError, ValueError):
        pass
    return False, used


def _wait(
    client: Any,
    *,
    frames: int,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    return _tap(
        client,
        {},
        frames=frames,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )


def box_ui_open_from_ram(ram: dict[str, int | float]) -> bool:
    """Item-box screen shares the START/ITEM pause-tree RAM signature."""
    from re1_rl.ram_skip import item_inventory_screen_from_ram

    return item_inventory_screen_from_ram(ram)


def probe_box_ui_open(client: Any) -> bool:
    ram = client.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
        ]
    )
    return box_ui_open_from_ram(ram)


def first_empty_inventory_slot(inventory: list[tuple[int, int]]) -> int | None:
    for i, (item_id, _qty) in enumerate(inventory):
        if int(item_id) == 0:
            return i
    return None


def _move(
    client: Any,
    direction: str,
    *,
    prev_hp: int,
    episode_start_hp: int,
    taps: int = 1,
) -> tuple[bool, int]:
    frames = 0
    for _ in range(max(0, int(taps))):
        died, f = _tap(
            client,
            {direction: True},
            frames=BOX_MOVE_TAP_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames
        died, f = _wait(
            client,
            frames=BOX_MOVE_SETTLE_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            return True, frames
    return False, frames


def _confirm_cross(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
    settle: int = TRANSFER_SETTLE_FRAMES,
) -> tuple[bool, int]:
    frames = 0
    died, f = _tap(
        client,
        {"cross": True},
        frames=TRANSFER_TAP_FRAMES,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        return True, frames
    died, f = _wait(
        client,
        frames=settle,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    return died, frames


def _navigate_inventory(
    client: Any,
    from_slot: int,
    to_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    frames = 0
    if int(from_slot) == int(to_slot):
        return False, 0
    try:
        moves = slot_nav_moves(int(from_slot), int(to_slot))
    except ValueError:
        # Fallback: walk via slot 0 when upward path would hit EXIT header.
        died, f = _move(
            client,
            "left",
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            taps=2,
        )
        frames += f
        if died:
            return True, frames
        died, f = _move(
            client,
            "up",
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            taps=3,
        )
        frames += f
        if died:
            return True, frames
        # Re-home may land on EXIT — nudge down onto slot 0.
        died, f = _move(
            client, "down", prev_hp=prev_hp, episode_start_hp=episode_start_hp, taps=1
        )
        frames += f
        if died:
            return True, frames
        moves = slot_nav_moves(0, int(to_slot))
        from_slot = 0
    for direction in moves:
        died, f = _move(
            client, direction, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            return True, frames
    return False, frames


def close_box_ui(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
    inv_cursor: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Dismiss the item box via EXIT (Cross), with Triangle fallback."""
    report: dict[str, Any] = {"ok": False, "path": "exit"}
    frames = 0
    if not probe_box_ui_open(client):
        report["ok"] = True
        report["skipped"] = True
        return False, 0, report

    cursor = max(0, min(INVENTORY_SLOTS - 1, int(inv_cursor)))
    # Bottom-right of the grid is the shortest path to EXIT (down from there).
    target = 5 if cursor <= 5 else 7
    try:
        target = 5
        died, f = _navigate_inventory(
            client,
            cursor,
            target,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
        pass

    for _ in range(EXIT_NAV_MAX_DOWNS):
        if not probe_box_ui_open(client):
            report["ok"] = True
            report["frames"] = frames
            return False, frames, report
        died, f = _move(
            client, "down", prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report

    died, f = _confirm_cross(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp, settle=CLOSE_SETTLE_FRAMES
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report
    if not probe_box_ui_open(client):
        report["ok"] = True
        report["frames"] = frames
        return False, frames, report

    # Triangle fallback (works when EXIT nav missed).
    report["path"] = "exit_then_triangle"
    for _ in range(CLOSE_MAX_ATTEMPTS):
        died, f = _tap(
            client,
            {"triangle": True},
            frames=CLOSE_TRIANGLE_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report
        died, f = _wait(
            client,
            frames=CLOSE_SETTLE_FRAMES,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report
        if not probe_box_ui_open(client):
            report["ok"] = True
            report["frames"] = frames
            return False, frames, report

    from re1_rl.inventory_menu_macro import close_item_screen

    died, f = close_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    still = probe_box_ui_open(client)
    report["path"] = "exit_triangle_start"
    report["ok"] = not still and not died
    report["died"] = bool(died)
    report["frames"] = frames
    return bool(died), frames, report


def execute_box_withdraw_ui(
    client: Any,
    box_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
    inv_cursor: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Withdraw ``box_slot`` via empty-inv-slot → box list → Cross.

    ``inv_cursor`` is the inventory slot the red cursor is on at entry
    (0 after open). On success, ``report['inv_cursor']`` is the destination
    slot (where the withdrawn item now sits).
    """
    report: dict[str, Any] = {
        "ok": False,
        "action": "withdraw",
        "box_slot": int(box_slot),
        "moved": None,
        "inv_cursor": int(inv_cursor),
    }
    frames = 0
    if not probe_box_ui_open(client):
        report["reason"] = "box_ui_closed"
        return False, 0, report

    slot = int(box_slot)
    if slot < 0 or slot >= BOX_SLOTS:
        report["reason"] = "bad_slot"
        return False, 0, report

    died, f = _wait(
        client,
        frames=POST_OPEN_SETTLE_FRAMES,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    inv_before = read_inventory(client)
    box_before = read_box(client)
    ok, reason = can_withdraw(inv_before, box_before, slot)
    if not ok:
        report["reason"] = reason
        return False, frames, report

    dest = first_empty_inventory_slot(inv_before)
    if dest is None:
        report["reason"] = "inventory_full"
        return False, frames, report
    report["dest_slot"] = int(dest)

    item_id, qty_before = box_before[slot]
    cursor = max(0, min(INVENTORY_SLOTS - 1, int(inv_cursor)))

    died, f = _navigate_inventory(
        client,
        cursor,
        int(dest),
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    # Cross on empty slot → cursor enters box list at slot 0.
    died, f = _confirm_cross(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    if slot > 0:
        died, f = _move(
            client,
            "down",
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            taps=slot,
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report

    died, f = _confirm_cross(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    inv_after = read_inventory(client)
    box_after = read_box(client)
    report["frames"] = frames
    if not probe_box_ui_open(client):
        report["reason"] = "box_ui_closed_mid_transfer"
        return False, frames, report

    src_after = box_after[slot] if slot < len(box_after) else (0, 0)
    dest_after = inv_after[dest] if dest < len(inv_after) else (0, 0)
    moved = False
    if dest_after[0] != 0 and inv_before[dest][0] == 0:
        moved = True
    elif src_after[0] == 0 and item_id != 0 and inv_after != inv_before:
        moved = True
    elif (
        src_after[0] == item_id
        and int(src_after[1]) < int(qty_before)
        and inv_after != inv_before
    ):
        moved = True

    # Reject knife/key exchange: destination must have gained the box item.
    if moved and dest_after[0] not in (0, int(item_id)):
        moved = False
        report["reason"] = "exchange_not_withdraw"
        report["inv_before"] = inv_before
        report["inv_after"] = inv_after
        report["box_before"] = box_before[:4]
        report["box_after"] = box_after[:4]
        return False, frames, report

    if not moved:
        report["reason"] = "transfer_no_effect"
        report["inv_before"] = inv_before
        report["inv_after"] = inv_after
        report["box_before"] = box_before[:4]
        report["box_after"] = box_after[:4]
        return False, frames, report

    moved_qty = max(1, int(qty_before) - int(src_after[1]))
    if dest_after[0] == int(item_id) and inv_before[dest][0] == 0:
        moved_qty = max(moved_qty, int(dest_after[1]))

    report["ok"] = True
    report["reason"] = ""
    report["moved"] = (item_id, moved_qty)
    # Cursor rests on the withdrawn stack in inventory.
    report["inv_cursor"] = int(dest)
    return False, frames, report


def execute_box_deposit_ui(
    client: Any,
    inv_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
    inv_cursor: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Deposit ``inv_slot`` via UI (occupied inv → Cross → box NOTHING → Cross)."""
    report: dict[str, Any] = {
        "ok": False,
        "action": "deposit",
        "inv_slot": int(inv_slot),
        "moved": None,
        "inv_cursor": int(inv_cursor),
    }
    frames = 0
    if not probe_box_ui_open(client):
        report["reason"] = "box_ui_closed"
        return False, 0, report

    slot = int(inv_slot)
    if slot < 0 or slot >= INVENTORY_SLOTS:
        report["reason"] = "bad_slot"
        return False, 0, report

    inv_before = read_inventory(client)
    box_before = read_box(client)
    ok, reason = can_deposit(inv_before, box_before, slot)
    if not ok:
        report["reason"] = reason
        return False, 0, report

    item_id, qty_before = inv_before[slot]
    cursor = max(0, min(INVENTORY_SLOTS - 1, int(inv_cursor)))
    died, f = _navigate_inventory(
        client,
        cursor,
        slot,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    died, f = _confirm_cross(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    # Box list: home to top, find first -Nothing- / empty (slot 0 often empty dest).
    died, f = _move(
        client,
        "up",
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        taps=16,
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    died, f = _confirm_cross(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    inv_after = read_inventory(client)
    box_after = read_box(client)
    report["frames"] = frames
    src_after = inv_after[slot] if slot < len(inv_after) else (0, 0)
    moved = False
    if src_after[0] == 0 and item_id != 0:
        moved = True
    elif src_after[0] == item_id and int(src_after[1]) < int(qty_before):
        moved = True
    if box_after == box_before:
        moved = False

    if not moved:
        report["reason"] = "transfer_no_effect"
        return False, frames, report

    report["ok"] = True
    report["reason"] = ""
    report["moved"] = (item_id, max(1, int(qty_before) - int(src_after[1])))
    report["inv_cursor"] = int(slot)
    return False, frames, report
