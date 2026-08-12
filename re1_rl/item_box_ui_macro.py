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

Policy: deposit allowlist (knife/heals everywhere; room 100 also bazooka bank).
"""

from __future__ import annotations

from typing import Any

from re1_rl.inventory_menu_macro import slot_nav_moves
from re1_rl.inventory_stacking import effective_transfer_qty
from re1_rl.item_box import (
    BOX_SLOTS,
    BOX_SLOTS_LIVE,
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
# Extra Ups from slot 0 WRAP through the 48-slot live list (0-15 → land on 33).
# Never Up-spam a fixed 15 from an unknown/zero resume — home exactly
# ``from_slot`` taps when the session cursor is known and > 0.
BOX_LIST_HOME_UPS = BOX_SLOTS - 1
# Aliases for QA probes (``scripts/_probe_box_withdraw_qa.py``).
MOVE_TAP_FRAMES = BOX_MOVE_TAP_FRAMES
MOVE_SETTLE_FRAMES = BOX_MOVE_SETTLE_FRAMES


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


def _deep_box_changed(
    before: list[tuple[int, int]],
    after: list[tuple[int, int]],
) -> bool:
    """True when any slot past the modeled window differs."""
    n = max(len(before), len(after), BOX_SLOTS_LIVE)
    for i in range(BOX_SLOTS, n):
        b = before[i] if i < len(before) else (0, 0)
        a = after[i] if i < len(after) else (0, 0)
        if (int(b[0]), int(b[1])) != (int(a[0]), int(a[1])):
            return True
    return False


def _first_empty_modeled_slot(box: list[tuple[int, int]]) -> int | None:
    for i, (iid, _q) in enumerate(box[:BOX_SLOTS]):
        if int(iid) == 0:
            return int(i)
    return None


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
    room_id: str | None = None,
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
    pollution = box_pollution_reason(
        box_live_after, room_id=room_id or report.get("room_id")
    )
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


def first_reachable_empty_inventory_slot(
    inventory: list[tuple[int, int]],
    *,
    from_slot: int = 0,
) -> int | None:
    """Lowest empty inv slot reachable under box-UI horizontal rules."""
    start = max(0, min(INVENTORY_SLOTS - 1, int(from_slot)))
    for i, (item_id, _qty) in enumerate(inventory[:INVENTORY_SLOTS]):
        if int(item_id) != 0:
            continue
        try:
            box_inventory_nav_moves(start, i, inventory)
        except ValueError:
            continue
        return i
    return None


def box_inventory_nav_moves(
    from_slot: int,
    to_slot: int,
    inventory: list[tuple[int, int]],
) -> list[str]:
    """D-pad path for the item-box inventory grid.

    Live RE1 DC box UI (QA 2026-08-11): **Up/Down work from any slot**, but
    **Left/Right only move when the current slot is empty**. Horizontal taps
    from an occupied cell are no-ops — the cursor stays put, so Cross deposits
    / withdraws the wrong item (shield_key / chemical mistaken for the target).

    Raises ``ValueError`` when no legal path exists (e.g. only slot 7 empty and
    slot 6 occupied — cannot enter column 1).
    """
    src = int(from_slot)
    dst = int(to_slot)
    if src == dst:
        return []
    if src < 0 or dst < 0 or src >= INVENTORY_SLOTS or dst >= INVENTORY_SLOTS:
        raise ValueError(f"box inv slot out of range: {src} -> {dst}")

    empty = {
        i
        for i, (iid, _q) in enumerate(inventory[:INVENTORY_SLOTS])
        if int(iid) == 0
    }
    # BFS on grid; horizontal edges only from empty cells.
    cols = 2
    start = src
    prev: dict[int, tuple[int, str]] = {}
    queue = [start]
    seen = {start}
    while queue:
        cur = queue.pop(0)
        if cur == dst:
            break
        cr, cc = divmod(cur, cols)
        candidates: list[tuple[int, str]] = []
        if cr > 0:
            candidates.append(((cr - 1) * cols + cc, "up"))
        if cr < (INVENTORY_SLOTS // cols) - 1:
            candidates.append(((cr + 1) * cols + cc, "down"))
        # Horizontal only when standing on an empty slot.
        if cur in empty:
            if cc > 0:
                candidates.append((cr * cols + (cc - 1), "left"))
            if cc < cols - 1:
                candidates.append((cr * cols + (cc + 1), "right"))
        for nxt, direction in candidates:
            if nxt in seen:
                continue
            seen.add(nxt)
            prev[nxt] = (cur, direction)
            queue.append(nxt)
    if dst not in prev and dst != start:
        raise ValueError(
            f"box inv unreachable {src} -> {dst} (empty={sorted(empty)})"
        )
    moves_rev: list[str] = []
    cur = dst
    while cur != start:
        p, direction = prev[cur]
        moves_rev.append(direction)
        cur = p
    moves_rev.reverse()
    return moves_rev


def deposit_inventory_nav_from(
    inv_cursor: int,
    inv_slot: int,
    *,
    trust_inv_cursor: bool,
) -> int:
    """Inventory grid start slot for deposit navigation.

    Returns the tracked inventory cursor unless callers opt into
    ``trust_inv_cursor=True`` (env has verified the UI rests there).
    """
    cursor = max(0, min(INVENTORY_SLOTS - 1, int(inv_cursor)))
    slot = int(inv_slot)
    if trust_inv_cursor:
        return cursor
    if cursor == slot:
        return cursor
    return cursor


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


_confirm_transfer = _confirm_cross


def _home_inventory(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Re-home inventory grid onto slot 0 without Left/Right pane switches.

    Box UI places the item list beside the inventory grid; Left/Right can leave
    the grid and park the cursor on a deep box slot. Vertical clamp only.
    """
    frames = 0
    for direction, taps in (("up", 4), ("down", 1)):
        died, f = _move(
            client,
            direction,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            taps=taps,
        )
        frames += f
        if died:
            return True, frames
    return False, frames


def _home_box_list(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
    from_slot: int = 0,
) -> tuple[bool, int]:
    """Move box-list cursor from ``from_slot`` up to slot 0 (exact taps).

    ``from_slot == 0`` is a no-op. Blind Up-spam from 0 wraps to deep slots
    (live: 15 Ups → slot 33) and deposits into NN-invisible RAM.
    """
    taps = max(0, min(BOX_SLOTS_LIVE - 1, int(from_slot)))
    if taps <= 0:
        return False, 0
    return _move(
        client,
        "up",
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        taps=taps,
    )


def _navigate_box_list(
    client: Any,
    from_slot: int,
    to_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Up/Down within the **modeled** box item list only (slots 0..BOX_SLOTS-1)."""
    src = max(0, min(BOX_SLOTS - 1, int(from_slot)))
    dst = max(0, min(BOX_SLOTS - 1, int(to_slot)))
    if int(to_slot) >= BOX_SLOTS or int(to_slot) < 0:
        # Caller must not request deep destinations.
        return False, 0
    delta = int(dst) - int(src)
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
    inventory: list[tuple[int, int]] | None = None,
) -> tuple[bool, int, str]:
    """Navigate inventory grid. Returns ``(died, frames, reason)``.

    When ``inventory`` is provided (box UI), use empty-slot-aware horizontal
    moves. ``reason`` is set on unreachable targets.
    """
    frames = 0
    if int(from_slot) == int(to_slot):
        return False, 0, ""
    try:
        if inventory is not None:
            moves = box_inventory_nav_moves(int(from_slot), int(to_slot), inventory)
        else:
            moves = slot_nav_moves(int(from_slot), int(to_slot))
    except ValueError as exc:
        return False, 0, f"inv_slot_unreachable:{exc}"
    for direction in moves:
        # Never Left/Right-fallback into the box list pane.
        died, f = _move(
            client, direction, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            return True, frames, ""
    return False, frames, ""


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
        died, f, _nav_reason = _navigate_inventory(
            client,
            cursor,
            5,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            inventory=None,  # EXIT path uses legacy nav
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
    room_id: str | None = None,
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
        "room_id": room_id,
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

    dest = first_reachable_empty_inventory_slot(
        inv_before, from_slot=int(inv_cursor)
    )
    if dest is None:
        # Distinguish "full" from "empty but unreachable under box-UI nav rules".
        if first_empty_inventory_slot(inv_before) is None:
            report["reason"] = "inventory_full"
        else:
            report["reason"] = "empty_slot_unreachable"
        return False, frames, report
    report["dest_slot"] = int(dest)

    item_id, qty_before = box_before[slot]
    cursor = max(0, min(INVENTORY_SLOTS - 1, int(inv_cursor)))

    died, f, nav_reason = _navigate_inventory(
        client,
        cursor,
        int(dest),
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        inventory=inv_before,
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report
    if nav_reason:
        report["reason"] = nav_reason
        report["frames"] = frames
        return False, frames, report

    # Must be on an empty inventory cell before Cross — otherwise we deposit
    # whatever is highlighted (beretta / shield_key live failures).
    inv_pre_cross = read_inventory(client)
    if dest >= len(inv_pre_cross) or int(inv_pre_cross[int(dest)][0]) != 0:
        report["reason"] = "withdraw_dest_not_empty"
        report["frames"] = frames
        return False, frames, report

    # Cross on empty slot → box list resumes at ``box_cursor``.
    died, f = _confirm_cross(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    # Only home when the tracked resume cursor is past slot 0. Exact Ups equal
    # to the resume index — never a fixed 15 (wraps 0→33 on the live 48-list).
    list_from = max(0, min(BOX_SLOTS - 1, int(box_cursor)))
    if list_from > 0:
        died, f = _home_box_list(
            client,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            from_slot=list_from,
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report
        list_from = 0

    if int(slot) >= BOX_SLOTS:
        report["reason"] = "box_slot_unmodeled"
        return False, frames, report

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

    from re1_rl.item_box import box_pollution_reason

    if _deep_box_changed(box_live_before, box_live_after):
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason="deep_box_write",
        )
        return False, frames, report

    pollution = box_pollution_reason(box_live_after, room_id=room_id)
    if pollution:
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason=pollution,
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
    room_id: str | None = None,
    trust_inv_cursor: bool = False,
    expected_item_id: int | None = None,
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
        "room_id": room_id,
    }
    frames = 0
    if not probe_box_ui_open(client):
        report["reason"] = "box_ui_closed"
        return False, 0, report

    slot = int(inv_slot)
    if slot < 0 or slot >= INVENTORY_SLOTS:
        report["reason"] = "bad_slot"
        return False, 0, report

    cold_session = (
        not trust_inv_cursor
        and int(inv_cursor) == 0
        and int(box_cursor) == 0
    )
    if cold_session:
        died, f = _home_inventory(
            client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report
        report["inv_homed"] = True

    inv_before = read_inventory(client)
    box_before = read_box(client)
    box_live_before = read_box_live(client)
    from re1_rl.item_box import box_pollution_reason

    pollution_before = box_pollution_reason(box_live_before, room_id=room_id)
    if pollution_before:
        report["reason"] = pollution_before
        report["exchange_detected"] = True
        return False, 0, report
    # Live path always enforces allowlist; keys are hard-denied inside can_deposit.
    ok, reason = can_deposit(
        inv_before,
        box_before,
        slot,
        room_id=room_id,
        enforce_allowlist=True,
    )
    if not ok:
        report["reason"] = reason
        return False, 0, report

    from re1_rl.item_box import is_key_item_id, is_deposit_allowed_item

    item_id, qty_before = inv_before[slot]
    if not is_deposit_allowed_item(int(item_id), room_id):
        report["reason"] = "key_item" if is_key_item_id(int(item_id)) else "not_allowlisted"
        return False, 0, report

    dest = _first_empty_modeled_slot(box_before)
    if dest is None or int(dest) >= BOX_SLOTS:
        report["reason"] = "box_full"
        return False, 0, report
    report["expected_dest"] = int(dest)

    if expected_item_id is not None and int(item_id) != int(expected_item_id):
        report["reason"] = "expected_item_mismatch"
        return False, 0, report

    # Snapshot key-item occupancy so a wrong-cursor Cross cannot silently bank them.
    keys_before = {
        int(iid)
        for iid, q in inv_before
        if int(iid) and is_key_item_id(int(iid)) and effective_transfer_qty(iid, q) > 0
    }

    # Extended-session nav: use empty-slot-aware paths. Prefer a direct path from
    # the tracked cursor; only re-anchor onto a reachable empty when the target
    # is otherwise unreachable (horizontal requires standing on empty). Never
    # invent an empty-cursor position — that deposited beretta/shield_key live.
    nav_from = deposit_inventory_nav_from(
        int(inv_cursor), int(slot), trust_inv_cursor=trust_inv_cursor
    )
    if cold_session:
        nav_from = 0
    try:
        box_inventory_nav_moves(nav_from, int(slot), inv_before)
        direct_ok = True
    except ValueError:
        direct_ok = False

    if not trust_inv_cursor and not direct_ok:
        anchor = first_reachable_empty_inventory_slot(
            inv_before, from_slot=nav_from
        )
        if anchor is None:
            report["reason"] = (
                f"inv_slot_unreachable:box inv unreachable "
                f"{nav_from} -> {slot} (need empty bridge)"
            )
            report["frames"] = frames
            return False, frames, report
        if int(anchor) != int(nav_from):
            died, f, nav_reason = _navigate_inventory(
                client,
                nav_from,
                int(anchor),
                prev_hp=prev_hp,
                episode_start_hp=episode_start_hp,
                inventory=inv_before,
            )
            frames += f
            if died:
                report["died"] = True
                report["frames"] = frames
                return True, frames, report
            if nav_reason:
                report["reason"] = nav_reason
                report["frames"] = frames
                return False, frames, report
            nav_from = int(anchor)
        report["rehomed"] = True
    elif not trust_inv_cursor:
        report["rehomed"] = False

    died, f, nav_reason = _navigate_inventory(
        client,
        nav_from,
        slot,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        inventory=inv_before,
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report
    if nav_reason:
        report["reason"] = nav_reason
        report["frames"] = frames
        return False, frames, report

    inv_at_cursor = read_inventory(client)
    if (
        slot >= len(inv_at_cursor)
        or int(inv_at_cursor[slot][0]) != int(item_id)
        or effective_transfer_qty(item_id, inv_at_cursor[slot][1]) <= 0
    ):
        report["reason"] = "inv_slot_drift"
        return False, frames, report

    died, f = _confirm_cross(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    # Resume at tracked box_cursor after Cross-in. Exact home only — never
    # Up×15 from slot 0 (wraps to live slot 33; memlog chemical@33).
    list_from = max(0, min(BOX_SLOTS - 1, int(box_cursor)))
    if list_from > 0:
        died, f = _home_box_list(
            client,
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            from_slot=list_from,
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report
        list_from = 0

    box_mid = read_box(client)
    dest = _first_empty_modeled_slot(box_mid)
    if dest is None or int(dest) >= BOX_SLOTS:
        report["reason"] = "box_full"
        return False, frames, report
    expected = report.get("expected_dest")
    if expected is not None and int(dest) != int(expected):
        report["reason"] = "dest_drift"
        return False, frames, report
    report["dest_slot"] = int(dest)

    if int(box_mid[int(dest)][0]) != 0:
        report["reason"] = "dest_occupied"
        return False, frames, report

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

    box_pre = read_box(client)
    if dest >= len(box_pre) or int(box_pre[int(dest)][0]) != 0:
        report["reason"] = "dest_occupied_pre_confirm"
        return False, frames, report

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

    # Any key that left the person is an automatic fail (wrong-cursor deposit).
    keys_after = {
        int(iid)
        for iid, q in inv_after
        if int(iid) and is_key_item_id(int(iid)) and effective_transfer_qty(iid, q) > 0
    }
    if keys_before - keys_after:
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason="key_item_deposited",
        )
        return False, frames, report

    dest_got = (
        dest < len(box_after)
        and int(dest) < BOX_SLOTS
        and int(box_pre[int(dest)][0]) == 0
        and int(box_after[int(dest)][0]) == int(item_id)
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
    if moved and int(box_after[int(dest)][0]) != int(item_id):
        moved = False

    if not moved:
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason=(
                "wrong_item_deposited"
                if dest_got and int(box_after[int(dest)][0]) != int(item_id)
                else "transfer_no_effect"
            ),
        )
        return False, frames, report

    if _deep_box_changed(box_live_before, box_live_after):
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason="deep_box_write",
        )
        return False, frames, report

    pollution = box_pollution_reason(box_live_after, room_id=room_id)
    if pollution:
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason=pollution,
        )
        return False, frames, report

    # Absolute contract: deposited item sits at the first empty modeled slot.
    if int(box_after[int(dest)][0]) != int(item_id) or int(dest) != int(
        report.get("expected_dest", dest)
    ):
        _finalize_transfer_failure(
            report,
            inv_before=inv_before,
            inv_after=inv_after,
            box_before=box_before,
            box_after=box_after,
            box_live_before=box_live_before,
            box_live_after=box_live_after,
            default_reason="dest_not_first_empty",
        )
        return False, frames, report

    moved_qty = max(1, before_units - after_units)
    # UI rests on the deposit source index after the transfer (inventory may
    # pack under that index). Prefer parking on a reachable empty so the next
    # withdraw Cross cannot fire on a compacted beretta/key.
    inv_out = int(slot)
    park = first_reachable_empty_inventory_slot(inv_after, from_slot=int(slot))
    if park is not None and int(park) != int(slot):
        died, f, nav_reason = _navigate_inventory(
            client,
            int(slot),
            int(park),
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            inventory=inv_after,
        )
        frames += f
        report["frames"] = frames
        if died:
            report["died"] = True
            return True, frames, report
        if not nav_reason:
            inv_out = int(park)
            report["parked_empty"] = inv_out
    report["ok"] = True
    report["reason"] = ""
    report["moved"] = (item_id, moved_qty)
    report["cursor_out"] = {"inv": inv_out, "box": int(dest)}
    report["inv_cursor"] = inv_out
    report["box_cursor"] = int(dest)
    return False, frames, report
