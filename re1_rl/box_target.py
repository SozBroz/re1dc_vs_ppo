"""Target leave-inventory for a planner ``use_box`` step.

Compares live 8-slot RAM to Muse ``held_on_exit`` and lists the only
deposits / withdraws that close the gap. Slot order does not matter.
Stackable ammo is compared by total qty; everything else by occupied count
(qty-0 weapons still count).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from re1_rl.inventory_stacking import is_stackable
from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS

_NAME_TO_ID = {canonical_item(name): item_id for item_id, name in ITEM_IDS.items()}


def _qty_pooled(item_id: int) -> bool:
    """Spare ammo stacks pool by qty. Weapons (incl. loaded beretta) are slots."""
    return is_stackable(item_id) and int(item_id) not in WEAPON_ITEM_IDS


def item_name_to_id(name: str) -> int | None:
    key = canonical_item(str(name or ""))
    return _NAME_TO_ID.get(key) if key else None


def parse_held_rows(rows: Any) -> list[tuple[int, int]]:
    """Muse dicts or RAM ``(item_id, qty)`` / ``(name, qty)`` → occupied pairs."""
    out: list[tuple[int, int]] = []
    for row in rows or []:
        if isinstance(row, dict):
            raw = row.get("item") or row.get("name")
            if not raw:
                continue
            iid = item_name_to_id(str(raw))
            if iid is None:
                continue
            out.append((int(iid), int(row.get("qty") or 0)))
            continue
        if not isinstance(row, (list, tuple)) or not row:
            continue
        first, qty = row[0], int(row[1]) if len(row) > 1 else 0
        if isinstance(first, str):
            iid = item_name_to_id(first)
            if iid is None:
                continue
            out.append((int(iid), qty))
        elif int(first):
            out.append((int(first), qty))
    return out


def _item_bag(slots: list[tuple[int, int]]) -> tuple[dict[int, int], Counter[int]]:
    ammo: dict[int, int] = {}
    other: Counter[int] = Counter()
    for iid, qty in slots:
        if not iid:
            continue
        if _qty_pooled(iid):
            ammo[iid] = ammo.get(iid, 0) + int(qty)
        else:
            other[int(iid)] += 1
    return ammo, other


def inventory_matches_target(inventory: Any, target: Any) -> bool:
    have = _item_bag(parse_held_rows(inventory))
    want = _item_bag(parse_held_rows(target))
    return have == want


def surplus_inventory_slots(inventory: Any, target: Any) -> list[int]:
    """0-based inv slots that must be deposited to reach ``target``."""
    raw = _ram_slots(inventory)
    want_ammo, want_other = _item_bag(parse_held_rows(target))
    remain_ammo = dict(want_ammo)
    remain_other = Counter(want_other)
    surplus: list[int] = []
    for index, (iid, qty) in enumerate(raw):
        if not iid:
            continue
        if _qty_pooled(iid):
            need = int(remain_ammo.get(iid, 0))
            if need <= 0:
                surplus.append(index)
                continue
            remain_ammo[iid] = max(0, need - int(qty))
            continue
        if remain_other[iid] > 0:
            remain_other[iid] -= 1
        else:
            surplus.append(index)
    return surplus


def needed_box_slots(inventory: Any, box: Any, target: Any) -> list[int]:
    """0-based box slots that still supply a missing target item."""
    raw_inv = _ram_slots(inventory)
    want_ammo, want_other = _item_bag(parse_held_rows(target))
    for iid, qty in raw_inv:
        if not iid:
            continue
        if _qty_pooled(iid):
            if want_ammo.get(iid, 0) > 0:
                take = min(int(qty), want_ammo[iid])
                want_ammo[iid] -= take
        elif want_other[iid] > 0:
            want_other[iid] -= 1
    needed: list[int] = []
    for index, (iid, _qty) in enumerate(_ram_slots(box)):
        if not iid:
            continue
        if _qty_pooled(iid) and want_ammo.get(iid, 0) > 0:
            needed.append(index)
        elif (not _qty_pooled(iid)) and want_other[iid] > 0:
            needed.append(index)
    return needed


def _ram_slots(rows: Any) -> list[tuple[int, int]]:
    """Keep empties and slot order. Muse dicts become compact occupied-only."""
    if not rows:
        return []
    first = rows[0]
    if isinstance(first, dict) and "slot" in first:
        slots = [(0, 0)] * 8
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get("item") or row.get("name")
            iid = item_name_to_id(str(raw)) if raw else None
            idx = int(row.get("slot") or 0) - 1
            if iid is None or idx < 0 or idx >= 8:
                continue
            slots[idx] = (int(iid), int(row.get("qty") or 0))
        return slots
    if isinstance(first, dict):
        # No slot numbers: occupied list, then pad.
        occupied = parse_held_rows(rows)
        while len(occupied) < 8:
            occupied.append((0, 0))
        return occupied[:8]
    out: list[tuple[int, int]] = []
    for row in rows:
        if isinstance(row, dict):
            raw = row.get("item") or row.get("name")
            iid = item_name_to_id(str(raw)) if raw else 0
            out.append((int(iid or 0), int(row.get("qty") or 0)))
            continue
        if isinstance(row, (list, tuple)) and row:
            first, qty = row[0], int(row[1]) if len(row) > 1 else 0
            if isinstance(first, str):
                iid = item_name_to_id(first) or 0
                out.append((int(iid), qty))
            else:
                out.append((int(first), qty))
            continue
        out.append((0, 0))
    return out
