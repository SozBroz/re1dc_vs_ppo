"""Authentic item-box UI macros (no magic RAM writes).

RE1 DC box flow (imperator scaffolding 2026-08-07 / deposit hunt 2026-08-08):
  - Interact opens the box; after the animation the cursor is on inventory
    slot 0 (top-left).
  - Withdraw: move to an **empty** inventory slot → Cross (cursor enters the
    box list; resume position is the last box slot selected this session,
    slot 0 after a fresh open) → Up/Down to the source box slot → Cross.
    The game places the item in the chosen empty slot and leaves the cursor
    on it.
  - Deposit: move to an **occupied** inventory slot → Cross (enters box list
    at ``box_cursor``) → Up/Down to the first empty box slot → Cross.
    Cross on an occupied box slot **exchanges** (avoid). Deposit onto
    ``-Nothing-`` is reliable after at least one successful transfer in the
    session (typical: withdraw ammo first); cold empty-box deposit is flaky.
  - Close: while the cursor is on the inventory grid, Triangle dismisses
    cleanly. EXIT (Down → Cross) is the fallback if Triangle misses.

Policy: deposit stays off until room-100 allowlist enablement.
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
    read_box_live,
    read_inventory,
)
from re1_rl.memory_map import (
    GAME_MODE,
    GAME_STATE,
    ITEM_BOX_UI_GAME_STATE,
    ITEM_BOX_UI_GAME_STATE_MASK,
    PAUSE_MENU_GAME_MODE,
    PLAYER_HP,
    player_died,
)

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


def _box_live_changed(
    before: list[tuple[int, int]],
    after: list[tuple[int, int]],
) -> bool:
    n = max(len(before), len(after))
    for i in range(n):
        b = before[i] if i < len(before) else (0, 0)
        a = after[i] if i < len(after) else (0, 0)
        if (int(b[0]), int(b[1])) != (int(a[0]), int(a[1])):
            return True
    return False


def _finalize_transfer_failure(
    report: dict[str, Any],
    *,
    inv_before: list[tuple[int, int]],
    inv_after: list[tuple[int, int]],
    box_before: list[tuple[int, int]],
    box_after: list[tuple[int, int]],
    box_live_before: list[tuple[int, int]],
    box_live_after: list[tuple[int, int]],
    default_reason: str,
) -> None:
    """Annotate failed transfers; never emit cursor_out on failure."""
    from re1_rl.item_box import box_pollution_reason

    report["reason"] = default_reason
    report["inv_before"] = inv_before[:4]
    report["inv_after"] = inv_after[:4]
    report["box_before"] = box_before[:4]
    report["box_after"] = box_after[:4]
    ram_changed = inv_before != inv_after or _box_live_changed(
        box_live_before, box_live_after
    )
    report["ram_changed"] = bool(ram_changed)
    pollution = box_pollution_reason(box_live_after)
    if pollution:
        report["reason"] = pollution
        report["exchange_detected"] = True
    elif ram_changed:
        report["reason"] = "exchange_detected"
        report["exchange_detected"] = True
    report.pop("inv_cursor", None)
    report.pop("box_cursor", None)


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
    """True only on the item-box UI (``gs`` mid-byte 0x90), not START/ITEM.

    Pickup Yes/No and the ITEM grid also sit in the pause tree; treating those
    as the box made BOX_CLOSE Triangle-spam over the chemical confirm dialog.
    """
    return (
        int(ram.get("game_mode", 0)) == PAUSE_MENU_GAME_MODE
        and (int(ram.get("game_state", 0)) & ITEM_BOX_UI_GAME_STATE_MASK)
        == ITEM_BOX_UI_GAME_STATE
    )


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


def _navigate_box_list(
    client: Any,
    from_slot: int,
    to_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Up/Down within the box item list (not the inventory grid)."""
    delta = int(to_slot) - int(from_slot)
    if delta == 0:
        return False, 0
    direction = "down" if delta > 0 else "up"
    return _move(
        client,
        direction,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        taps=abs(delta),
    )


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
    """Dismiss the item box: Triangle on inventory first, EXIT as fallback.

    After a withdraw the cursor rests on an inventory slot — Triangle closes
    without EXIT navigation. ``inv_cursor`` is kept for the EXIT fallback path.
    """
    report: dict[str, Any] = {"ok": False, "path": "triangle"}
    frames = 0
    if not probe_box_ui_open(client):
        report["ok"] = True
        report["skipped"] = True
        return False, 0, report

    # Primary: Triangle while focused on the inventory grid.
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

    # Fallback: navigate to EXIT and Cross (cursor may be on the box list).
    report["path"] = "triangle_then_exit"
    cursor = max(0, min(INVENTORY_SLOTS - 1, int(inv_cursor)))
    try:
        died, f = _navigate_inventory(
            client,
            cursor,
            5,
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
        client,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        settle=CLOSE_SETTLE_FRAMES,
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
    report["path"] = "triangle_exit_start"
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
    box_cursor: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Withdraw ``box_slot`` via empty-inv-slot → box list → Cross.

    ``inv_cursor`` is the inventory slot the red cursor is on at entry
    (0 after open). ``box_cursor`` is where the box list resumes when Cross
    enters it (last selected box slot this session; 0 after fresh open).
    On success, ``report['inv_cursor']`` is the destination inventory slot
    and ``report['box_cursor']`` is the box slot just taken.
    """
    report: dict[str, Any] = {
        "ok": False,
        "action": "withdraw",
        "box_slot": int(box_slot),
        "moved": None,
        "cursor_in": {"inv": int(inv_cursor), "box": int(box_cursor)},
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
    box_live_before = read_box_live(client)
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

    # Cross on empty slot → box list resumes at ``box_cursor``.
    died, f = _confirm_cross(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    list_from = max(0, min(BOX_SLOTS - 1, int(box_cursor)))
    died, f = _navigate_box_list(
        client,
        list_from,
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

    inv_after = read_inventory(client)
    box_after = read_box(client)
    box_live_after = read_box_live(client)
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
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason="exchange_not_withdraw",
        )
        return False, frames, report

    if not moved:
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason="transfer_no_effect",
        )
        return False, frames, report

    moved_qty = max(1, int(qty_before) - int(src_after[1]))
    if dest_after[0] == int(item_id) and inv_before[dest][0] == 0:
        moved_qty = max(moved_qty, int(dest_after[1]))

    report["ok"] = True
    report["reason"] = ""
    report["moved"] = (item_id, moved_qty)
    report["cursor_out"] = {"inv": int(dest), "box": int(slot)}
    report["inv_cursor"] = int(dest)
    report["box_cursor"] = int(slot)
    return False, frames, report


def execute_box_deposit_ui(
    client: Any,
    inv_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
    inv_cursor: int = 0,
    box_cursor: int = 0,
) -> tuple[bool, int, dict[str, Any]]:
    """Deposit ``inv_slot`` via occupied inv → Cross → empty box → Cross.

    ``box_cursor`` is where the box list resumes on Cross (same session
    tracking as withdraw). Destination is the first empty modeled box slot
    (never exchange onto an occupied entry). Env must pass tracked cursors
    from the prior transfer; failures annotate ``exchange_detected``.
    """
    report: dict[str, Any] = {
        "ok": False,
        "action": "deposit",
        "inv_slot": int(inv_slot),
        "moved": None,
        "cursor_in": {"inv": int(inv_cursor), "box": int(box_cursor)},
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
    box_live_before = read_box_live(client)
    ok, reason = can_deposit(inv_before, box_before, slot)
    if not ok:
        report["reason"] = reason
        return False, 0, report

    dest = next(
        (i for i, (iid, _q) in enumerate(box_before) if int(iid) == 0),
        None,
    )
    if dest is None:
        report["reason"] = "box_full"
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

    list_from = max(0, min(BOX_SLOTS - 1, int(box_cursor)))
    died, f = _navigate_box_list(
        client,
        list_from,
        int(dest),
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

    inv_after = read_inventory(client)
    box_after = read_box(client)
    box_live_after = read_box_live(client)
    report["frames"] = frames
    report["dest_slot"] = int(dest)

    dest_got = (
        dest < len(box_after)
        and int(box_before[dest][0]) == 0
        and int(box_after[dest][0]) == int(item_id)
    )
    before_units = sum(
        1 if (int(q) <= 0 and int(iid) == int(item_id)) else int(q)
        for iid, q in inv_before
        if int(iid) == int(item_id)
    )
    after_units = sum(
        1 if (int(q) <= 0 and int(iid) == int(item_id)) else int(q)
        for iid, q in inv_after
        if int(iid) == int(item_id)
    )
    moved = bool(dest_got) and after_units < before_units

    if not moved:
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason="transfer_no_effect",
        )
        return False, frames, report

    moved_qty = max(1, before_units - after_units)
    report["ok"] = True
    report["reason"] = ""
    report["moved"] = (item_id, moved_qty)
    report["cursor_out"] = {"inv": int(slot), "box": int(dest)}
    report["inv_cursor"] = int(slot)
    report["box_cursor"] = int(dest)
    return False, frames, report
