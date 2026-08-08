"""Authentic item-box UI macros (no magic RAM writes).

While the box screen is open the policy picks withdraw / deposit / close.
Withdraw and deposit only select the *source* slot; the game places the item
in the first legal destination (empty slot or merge stack).

Cursor assumptions (RE1 DC item box, confirmed by prior UI hunts):
  - Open homes on inventory slot 0 (left pane).
  - ``right`` moves to the box list (right pane), top entry.
  - ``up`` / ``down`` walk the box list; ``left`` returns to inventory.
  - Inventory is the same 2×4 grid as the START/ITEM screen.
  - ``cross`` transfers the highlighted item to the other pane.
  - ``triangle`` closes the box UI.
"""

from __future__ import annotations

from typing import Any

from re1_rl.inventory_menu_macro import (
    MOVE_SETTLE_FRAMES,
    MOVE_TAP_FRAMES,
    slot_nav_moves,
)
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
TRANSFER_TAP_FRAMES = 12
TRANSFER_SETTLE_FRAMES = 40
CLOSE_TRIANGLE_FRAMES = 12
CLOSE_SETTLE_FRAMES = 40
CLOSE_MAX_ATTEMPTS = 6
BOX_LIST_HOME_UPS = 20


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
    # abort_on_zero_hp=False: menu frames have been seen to false-trip death.
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


def close_box_ui(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, dict[str, Any]]:
    """Dismiss the item box (Triangle, then Start fallback)."""
    report: dict[str, Any] = {"ok": False, "path": "triangle"}
    frames = 0
    if not probe_box_ui_open(client):
        report["ok"] = True
        report["skipped"] = True
        return False, 0, report

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

    # Start dismiss (same path as orphan ITEM close).
    from re1_rl.inventory_menu_macro import close_item_screen

    died, f = close_item_screen(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    still = probe_box_ui_open(client)
    report["path"] = "triangle_then_start"
    report["ok"] = not still and not died
    report["died"] = bool(died)
    report["frames"] = frames
    return bool(died), frames, report


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
    return False, frames


def _home_box_list(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """From inventory home: enter box pane and scroll to the top entry."""
    frames = 0
    died, f = _move(
        client, "right", prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        return True, frames
    died, f = _move(
        client,
        "up",
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        taps=BOX_LIST_HOME_UPS,
    )
    frames += f
    return died, frames


def _home_inventory(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int]:
    """Return cursor to inventory slot 0 (left pane)."""
    frames = 0
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
    # From an unknown inv slot, walk toward slot 0 via up/left spam.
    died, f = _move(
        client,
        "up",
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        taps=4,
    )
    frames += f
    if died:
        return True, frames
    died, f = _move(
        client,
        "left",
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
        taps=2,
    )
    frames += f
    return died, frames


def _confirm_transfer(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
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
        frames=TRANSFER_SETTLE_FRAMES,
        prev_hp=prev_hp,
        episode_start_hp=episode_start_hp,
    )
    frames += f
    return died, frames


def execute_box_withdraw_ui(
    client: Any,
    box_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, dict[str, Any]]:
    """Withdraw ``box_slot`` via UI. Agent only chose the source item.

    Destination (merge / first empty inv slot) is chosen by the game.
    Returns ``(died, frames, report)``.
    """
    report: dict[str, Any] = {
        "ok": False,
        "action": "withdraw",
        "box_slot": int(box_slot),
        "moved": None,
    }
    frames = 0
    if not probe_box_ui_open(client):
        report["reason"] = "box_ui_closed"
        return False, 0, report

    slot = int(box_slot)
    if slot < 0 or slot >= BOX_SLOTS:
        report["reason"] = "bad_slot"
        return False, 0, report

    inv_before = read_inventory(client)
    box_before = read_box(client)
    ok, reason = can_withdraw(inv_before, box_before, slot)
    if not ok:
        report["reason"] = reason
        return False, 0, report

    item_id, qty_before = box_before[slot]
    died, f = _home_box_list(
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

    died, f = _confirm_transfer(
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

    # Success: source emptied or qty dropped, and inventory accepted something.
    src_after = box_after[slot] if slot < len(box_after) else (0, 0)
    moved = False
    if src_after[0] == 0 and item_id != 0:
        moved = True
    elif src_after[0] == item_id and int(src_after[1]) < int(qty_before):
        moved = True
    if inv_after == inv_before:
        moved = False

    if not moved:
        report["reason"] = "transfer_no_effect"
        report["inv_before"] = inv_before
        report["inv_after"] = inv_after
        report["box_before"] = box_before[:4]
        report["box_after"] = box_after[:4]
        return False, frames, report

    report["ok"] = True
    report["reason"] = ""
    report["moved"] = (item_id, max(1, int(qty_before) - int(src_after[1])))
    return False, frames, report


def execute_box_deposit_ui(
    client: Any,
    inv_slot: int,
    *,
    prev_hp: int,
    episode_start_hp: int,
) -> tuple[bool, int, dict[str, Any]]:
    """Deposit ``inv_slot`` via UI. Destination = first empty box slot (game)."""
    report: dict[str, Any] = {
        "ok": False,
        "action": "deposit",
        "inv_slot": int(inv_slot),
        "moved": None,
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
    died, f = _home_inventory(
        client, prev_hp=prev_hp, episode_start_hp=episode_start_hp
    )
    frames += f
    if died:
        report["died"] = True
        report["frames"] = frames
        return True, frames, report

    try:
        moves = slot_nav_moves(0, slot)
    except ValueError:
        report["reason"] = "nav_error"
        report["frames"] = frames
        return False, frames, report

    for direction in moves:
        died, f = _move(
            client, direction, prev_hp=prev_hp, episode_start_hp=episode_start_hp
        )
        frames += f
        if died:
            report["died"] = True
            report["frames"] = frames
            return True, frames, report

    died, f = _confirm_transfer(
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
    return False, frames, report
