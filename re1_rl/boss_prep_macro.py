"""Verified boss-prep item-box sequences (authentic UI, no navigation macros).

Room ``100`` banking: deposit grenade launcher + acid rounds with forced
inventory rehome and RAM receipts. Training exposes one high-level box action
(``BOX_BANK_BOSS_ACTION``) that runs the full preset when preflight passes.
"""

from __future__ import annotations

from typing import Any

from re1_rl.item_box import (
    BAZOOKA_AMMO_IDS,
    BAZOOKA_WEAPON_IDS,
    INVENTORY_SLOTS,
    box_pollution_reason,
    can_deposit,
    read_box,
    read_box_live,
    read_inventory,
)
from re1_rl.item_box_ui_macro import execute_box_deposit_ui, probe_box_ui_open

# Canonical room-100 boss bank order (weapon first, then ammo pack).
ROOM_100_BOSS_BANK_DEPOSIT_IDS: tuple[int, ...] = (0x07, 0x11)  # bazooka_acid, acid_rounds
HANDGUN_BULLETS_ID = 0x0B


def _slot_for_item(
    inventory: list[tuple[int, int]], item_id: int
) -> int | None:
    want = int(item_id)
    for i, (iid, qty) in enumerate(inventory):
        if int(iid) == want and effective_qty(iid, qty) > 0:
            return int(i)
    return None


def effective_qty(item_id: int, qty: int) -> int:
    from re1_rl.inventory_stacking import effective_transfer_qty

    return int(effective_transfer_qty(int(item_id), int(qty)))


def _count_item(
    slots: list[tuple[int, int]], item_id: int, *, limit: int | None = None
) -> int:
    want = int(item_id)
    total = 0
    n = len(slots) if limit is None else min(len(slots), int(limit))
    for i in range(n):
        iid, qty = slots[i]
        if int(iid) == want:
            total += max(1, int(qty)) if int(qty) <= 0 and int(iid) != 0 else int(qty)
    return total


def room100_boss_bank_preflight(
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
    *,
    room_id: str | None,
    box_live: list[tuple[int, int]] | None = None,
) -> tuple[bool, str]:
    """True when the verified room-100 boss bank preset may run."""
    rid = str(room_id or "").strip().upper()
    if rid != "100":
        return False, "wrong_room"
    live = box_live if box_live is not None else box
    pollution = box_pollution_reason(live)
    if pollution:
        return False, pollution
    for iid, _qty in box[:16]:
        if int(iid) == HANDGUN_BULLETS_ID:
            return False, "handgun_in_box"
    for item_id in ROOM_100_BOSS_BANK_DEPOSIT_IDS:
        slot = _slot_for_item(inventory, item_id)
        if slot is None:
            return False, f"missing_inv:{item_id:#x}"
        ok, reason = can_deposit(
            inventory, box, int(slot), room_id=rid, enforce_allowlist=True
        )
        if not ok:
            return False, reason or "deposit_blocked"
    empty_box = sum(1 for iid, _q in box[:16] if int(iid) == 0)
    if empty_box < len(ROOM_100_BOSS_BANK_DEPOSIT_IDS):
        return False, "box_full"
    return True, ""


def execute_room100_boss_bank_ui(
    client: Any,
    *,
    prev_hp: int,
    episode_start_hp: int,
    inv_cursor: int = 0,
    box_cursor: int = 0,
    room_id: str | None = "100",
) -> tuple[bool, int, dict[str, Any]]:
    """Run the full room-100 boss bank preset with per-step verification."""
    report: dict[str, Any] = {
        "ok": False,
        "action": "boss_bank_room100",
        "transfers": [],
        "room_id": str(room_id or ""),
    }
    frames = 0
    if not probe_box_ui_open(client):
        report["reason"] = "box_ui_closed"
        return False, 0, report

    inv0 = read_inventory(client)
    box0 = read_box(client)
    box_live0 = read_box_live(client)
    ok, reason = room100_boss_bank_preflight(
        inv0, box0, room_id=room_id, box_live=box_live0
    )
    if not ok:
        report["reason"] = reason
        return False, 0, report

    hg_before = _count_item(inv0, HANDGUN_BULLETS_ID) + _count_item(
        box_live0, HANDGUN_BULLETS_ID, limit=16
    )
    inv_cursor_work = int(inv_cursor)
    box_cursor_work = int(box_cursor)

    for item_id in ROOM_100_BOSS_BANK_DEPOSIT_IDS:
        inv_now = read_inventory(client)
        slot = _slot_for_item(inv_now, item_id)
        if slot is None:
            report["reason"] = f"missing_inv:{item_id:#x}"
            return False, frames, report
        inv_before = list(inv_now)
        box_before = read_box(client)
        died, step_frames, step = execute_box_deposit_ui(
            client,
            int(slot),
            prev_hp=prev_hp,
            episode_start_hp=episode_start_hp,
            inv_cursor=inv_cursor_work,
            box_cursor=box_cursor_work,
            room_id=room_id,
            trust_inv_cursor=False,
            expected_item_id=int(item_id),
        )
        frames += int(step_frames)
        report["transfers"].append(step)
        if died:
            report["died"] = True
            report["reason"] = "died"
            report["frames"] = frames
            return True, frames, report
        if not step.get("ok"):
            report["reason"] = step.get("reason") or "transfer_failed"
            report["frames"] = frames
            return False, frames, report
        moved = step.get("moved")
        if not moved or int(moved[0]) != int(item_id):
            report["reason"] = "wrong_item_moved"
            report["frames"] = frames
            return False, frames, report
        inv_after = read_inventory(client)
        box_after = read_box(client)
        box_live_step = read_box_live(client)
        pol_step = box_pollution_reason(box_live_step)
        if pol_step:
            report["reason"] = pol_step
            report["frames"] = frames
            return False, frames, report
        if _count_item(inv_after, int(item_id)) >= _count_item(
            inv_before, int(item_id)
        ):
            report["reason"] = "inv_not_reduced"
            report["frames"] = frames
            return False, frames, report
        dest = step.get("dest_slot")
        if dest is None or int(dest) >= 16:
            report["reason"] = "dest_unmodeled"
            report["frames"] = frames
            return False, frames, report
        if int(box_after[int(dest)][0]) != int(item_id):
            report["reason"] = "wrong_box_dest"
            report["frames"] = frames
            return False, frames, report
        inv_cursor_work = int(step.get("inv_cursor", slot))
        box_cursor_work = int(step.get("box_cursor", box_cursor_work))

    inv_final = read_inventory(client)
    box_final = read_box(client)
    box_live_final = read_box_live(client)
    hg_after = _count_item(inv_final, HANDGUN_BULLETS_ID) + _count_item(
        box_live_final, HANDGUN_BULLETS_ID, limit=16
    )
    if hg_after != hg_before:
        report["reason"] = "handgun_bullets_changed"
        report["frames"] = frames
        return False, frames, report
    pollution = box_pollution_reason(box_live_final)
    if pollution:
        report["reason"] = pollution
        report["frames"] = frames
        return False, frames, report
    free = sum(1 for iid, _q in inv_final if int(iid) == 0)
    report["inventory_free_slots"] = int(free)
    report["ok"] = True
    report["reason"] = ""
    report["frames"] = frames
    report["inv_cursor"] = inv_cursor_work
    report["box_cursor"] = box_cursor_work
    report["box_before"] = box0[:4]
    report["box_after"] = box_final[:4]
    return False, frames, report


def boss_bank_item_ids_for_room(room_id: str | None) -> frozenset[int]:
    rid = str(room_id or "").strip().upper()
    if rid == "100":
        return frozenset(ROOM_100_BOSS_BANK_DEPOSIT_IDS)
    return frozenset(BAZOOKA_WEAPON_IDS | BAZOOKA_AMMO_IDS)
