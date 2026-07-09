"""RE1 DC inventory COMBINE: herbs, ammo pile merge, weapon reload."""

from __future__ import annotations

from typing import Any

from re1_rl.ammo_accounting import WEAPON_AMMO_ITEM, WEAPON_CLIP_CAPACITY
from re1_rl.herb_combine import combine_product, plan_combine as plan_herb_combine
from re1_rl.inventory_stacking import is_stackable, stack_limit
from re1_rl.memory_map import (
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    INVENTORY_BASE,
    WEAPON_ITEM_IDS,
)


def _plan_ammo_merge(
    inventory: list[tuple[int, int]],
    first: int,
    second: int,
) -> tuple[list[tuple[int, int]], int, int] | None:
    """Merge ``second`` pile into ``first`` when same stackable ammo id."""
    id1, q1 = inventory[first]
    id2, q2 = inventory[second]
    if id1 != id2 or id1 == 0 or not is_stackable(id1):
        return None
    if q1 <= 0 or q2 <= 0:
        return None
    limit = stack_limit(id1)
    if q1 >= limit:
        return None
    moved = min(int(q2), limit - int(q1))
    if moved <= 0:
        return None
    new_inv = list(inventory)
    new_inv[first] = (id1, int(q1) + moved)
    remaining = int(q2) - moved
    new_inv[second] = (id2, remaining) if remaining > 0 else (0, 0)
    return new_inv, first, id1


def _plan_weapon_reload(
    inventory: list[tuple[int, int]],
    first: int,
    second: int,
) -> tuple[list[tuple[int, int]], int, int] | None:
    """Reload weapon from ammo stack (order: first pick, second pick)."""
    for weapon_slot, ammo_slot in ((first, second), (second, first)):
        wid, wq = inventory[weapon_slot]
        aid, aq = inventory[ammo_slot]
        if int(wid) not in WEAPON_ITEM_IDS or int(wid) == 0x01:
            continue
        expected = WEAPON_AMMO_ITEM.get(int(wid))
        if expected is None or int(aid) != int(expected):
            continue
        if int(aq) <= 0:
            continue
        clip = WEAPON_CLIP_CAPACITY.get(int(wid), 1)
        if int(wq) >= clip:
            continue
        moved = min(int(aq), clip - int(wq))
        if moved <= 0:
            continue
        new_inv = list(inventory)
        new_inv[weapon_slot] = (int(wid), int(wq) + moved)
        remaining = int(aq) - moved
        new_inv[ammo_slot] = (int(aid), remaining) if remaining > 0 else (0, 0)
        return new_inv, weapon_slot, int(wid)
    return None


def plan_combine(
    inventory: list[tuple[int, int]],
    first_slot: int,
    second_slot: int,
) -> tuple[list[tuple[int, int]], int, int] | None:
    """Ordered COMBINE: ``first_slot`` is cursor A, ``second_slot`` is cursor B."""
    if first_slot == second_slot:
        return None
    if (
        first_slot < 0
        or second_slot < 0
        or first_slot >= len(inventory)
        or second_slot >= len(inventory)
    ):
        return None

    herb = plan_herb_combine(inventory, first_slot, second_slot)
    if herb is not None:
        return herb

    ammo = _plan_ammo_merge(inventory, first_slot, second_slot)
    if ammo is not None:
        return ammo

    return _plan_weapon_reload(inventory, first_slot, second_slot)


def can_combine_slots(
    inventory: list[tuple[int, int]],
    first_slot: int,
    second_slot: int,
) -> bool:
    return plan_combine(inventory, first_slot, second_slot) is not None


def any_valid_combine(inventory: list[tuple[int, int]]) -> bool:
    for i in range(len(inventory)):
        for j in range(len(inventory)):
            if i != j and can_combine_slots(inventory, i, j):
                return True
    return False


def slot_legal_as_first(inventory: list[tuple[int, int]], slot: int) -> bool:
    if slot < 0 or slot >= len(inventory):
        return False
    for other in range(len(inventory)):
        if other != slot and can_combine_slots(inventory, slot, other):
            return True
    return False


def slot_legal_as_second(
    inventory: list[tuple[int, int]],
    first_slot: int,
    slot: int,
) -> bool:
    return slot != first_slot and can_combine_slots(inventory, first_slot, slot)


def apply_combine(
    bridge: Any,
    first_slot: int,
    second_slot: int,
    *,
    equipped_weapon_id: int,
    equipped_slot_0based: int | None,
) -> dict[str, Any]:
    from re1_rl.item_box import _slot_write_fields, read_inventory

    inventory = read_inventory(bridge)
    planned = plan_combine(inventory, first_slot, second_slot)
    if planned is None:
        return {"ok": False, "reason": "invalid_combine", "product": None, "unequipped": False}

    new_inv, _dest, product = planned
    fields = _slot_write_fields("inv", INVENTORY_BASE, new_inv)

    unequipped = False
    if equipped_slot_0based is not None and int(equipped_weapon_id) != 0:
        for slot in (first_slot, second_slot):
            if slot == int(equipped_slot_0based):
                if inventory[slot][0] == int(equipped_weapon_id):
                    unequipped = True
    if unequipped:
        fields.append(("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8", 0))
        fields.append(("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8", 0))

    bridge.write_ram(fields)
    return {
        "ok": True,
        "reason": "",
        "product": product,
        "unequipped": unequipped,
    }
