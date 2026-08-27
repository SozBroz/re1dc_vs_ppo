"""Target leave-inventory for a planner ``use_box`` step.

Compares live 8-slot RAM to Muse ``held_on_exit`` and lists the only
deposits / withdraws that close the gap. Slot order does not matter.
Stackable ammo is a minimum: clips plus rounds loaded in the matching gun
count together, and extra ammo of that type is fine. Everything else is
compared by occupied count (qty-0 weapons still count).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from re1_rl.ammo_accounting import WEAPON_AMMO_ITEM
from re1_rl.inventory_stacking import is_stackable
from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS

_NAME_TO_ID = {canonical_item(name): item_id for item_id, name in ITEM_IDS.items()}


def _qty_pooled(item_id: int) -> bool:
    """Spare ammo stacks pool by qty. Weapons (incl. loaded beretta) are slots."""
    return is_stackable(item_id) and int(item_id) not in WEAPON_ITEM_IDS


def _ammo_id_for_weapon(item_id: int) -> int | None:
    raw = WEAPON_AMMO_ITEM.get(int(item_id) & 0xFF)
    return int(raw) if raw is not None else None


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
    """Ammo pools clips plus loaded gun qty; other items are occupancy counts."""
    ammo: dict[int, int] = {}
    other: Counter[int] = Counter()
    for iid, qty in slots:
        if not iid:
            continue
        if _qty_pooled(iid):
            ammo[iid] = ammo.get(iid, 0) + int(qty)
            continue
        other[int(iid)] += 1
        ammo_id = _ammo_id_for_weapon(iid)
        if ammo_id is not None and int(qty) > 0:
            ammo[ammo_id] = ammo.get(ammo_id, 0) + int(qty)
    return ammo, other


def inventory_matches_target(inventory: Any, target: Any) -> bool:
    have_ammo, have_other = _item_bag(parse_held_rows(inventory))
    want_ammo, want_other = _item_bag(parse_held_rows(target))
    if have_other != want_other:
        return False
    for ammo_id, need in want_ammo.items():
        if int(have_ammo.get(ammo_id, 0)) < int(need):
            return False
    for ammo_id, qty in have_ammo.items():
        if int(qty) > 0 and ammo_id not in want_ammo:
            return False
    return True


def surplus_inventory_slots(inventory: Any, target: Any) -> list[int]:
    """0-based inv slots that must be deposited to reach ``target``."""
    raw = _ram_slots(inventory)
    have_ammo, _have_other = _item_bag(raw)
    want_ammo, want_other = _item_bag(parse_held_rows(target))
    remain_other = Counter(want_other)
    surplus: list[int] = []
    for index, (iid, _qty) in enumerate(raw):
        if not iid:
            continue
        if _qty_pooled(iid):
            need = int(want_ammo.get(iid, 0))
            # Required ammo is a floor (clip + gun). Extra of that type stays.
            if need > 0 and int(have_ammo.get(iid, 0)) >= need:
                continue
            if need > 0:
                continue
            surplus.append(index)
            continue
        if remain_other[iid] > 0:
            remain_other[iid] -= 1
        else:
            surplus.append(index)
    return surplus


def needed_box_slots(inventory: Any, box: Any, target: Any) -> list[int]:
    """0-based box slots that still supply a missing target item."""
    raw_inv = _ram_slots(inventory)
    have_ammo, _have_other = _item_bag(raw_inv)
    want_ammo, want_other = _item_bag(parse_held_rows(target))
    remain_ammo = {
        ammo_id: max(0, int(need) - int(have_ammo.get(ammo_id, 0)))
        for ammo_id, need in want_ammo.items()
    }
    remain_other = Counter(want_other)
    for iid, _qty in raw_inv:
        if not iid or _qty_pooled(iid):
            continue
        if remain_other[iid] > 0:
            remain_other[iid] -= 1
    needed: list[int] = []
    for index, (iid, _qty) in enumerate(_ram_slots(box)):
        if not iid:
            continue
        if _qty_pooled(iid) and remain_ammo.get(iid, 0) > 0:
            needed.append(index)
        elif (not _qty_pooled(iid)) and remain_other[iid] > 0:
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
