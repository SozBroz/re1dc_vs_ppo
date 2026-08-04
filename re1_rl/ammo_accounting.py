"""Ammo totals for RE1 PS1 Director's Cut attack / combine gating."""

from __future__ import annotations

from typing import Any

from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS

# Reserve ammo item id per weapon (None = knife / no ammo item).
WEAPON_AMMO_ITEM: dict[int, int | None] = {
    0x01: None,
    0x02: 0x0B,  # beretta + spare handgun_bullets
    0x03: 0x0C,  # shotgun + shells
    0x04: 0x0D,  # colt python dumdum
    0x05: 0x0E,  # colt python magnum
    0x06: 0x0F,  # flamethrower fuel
    0x07: 0x11,  # acid launcher
    0x08: 0x10,  # explosive launcher
    0x09: 0x12,  # flame launcher
    0x0A: 0x10,  # rocket launcher (explosive rounds)
}

# Rounds the weapon slot itself can hold after a COMBINE reload (PS1 DC).
WEAPON_CLIP_CAPACITY: dict[int, int] = {
    0x02: 15,  # PS1 DC handgun magazine (live combine QuickSave0)
    0x03: 7,
    0x04: 6,
    0x05: 6,
    0x06: 220,
    0x07: 1,
    0x08: 1,
    0x09: 1,
    0x0A: 1,
}


def loaded_weapon_ammo(
    inventory: list[tuple[int, int]],
    weapon_id: int,
    equipped_slot_0based: int | None = None,
) -> int:
    """Rounds in the equipped weapon slot only (not reserve ammo piles)."""
    wid = int(weapon_id) & 0xFF
    if wid == 0x01:
        return 1
    if wid not in WEAPON_ITEM_IDS:
        return 0
    if equipped_slot_0based is not None:
        slot = int(equipped_slot_0based)
        if 0 <= slot < len(inventory):
            iid, qty = inventory[slot]
            if int(iid) & 0xFF == wid:
                return int(qty)
        return 0
    for iid, qty in inventory:
        if int(iid) & 0xFF == wid:
            return int(qty)
    return 0


def total_fireable_ammo(
    inventory: list[tuple[int, int]],
    weapon_id: int,
) -> int:
    """Sum loaded weapon qty plus matching reserve ammo stacks."""
    wid = int(weapon_id) & 0xFF
    if wid == 0x01:
        return 1
    if wid not in WEAPON_ITEM_IDS:
        return 0
    ammo_id = WEAPON_AMMO_ITEM.get(wid)
    total = 0
    for item_id, qty in inventory:
        iid = int(item_id) & 0xFF
        q = int(qty)
        if q <= 0:
            continue
        if iid == wid or (ammo_id is not None and iid == int(ammo_id)):
            total += q
    return total


def reserve_ammo(
    inventory: list[tuple[int, int]],
    weapon_id: int,
) -> int:
    """Matching reserve stacks only; excludes rounds loaded in weapon slots."""
    ammo_id = WEAPON_AMMO_ITEM.get(int(weapon_id) & 0xFF)
    if ammo_id is None:
        return 0
    return sum(
        max(0, int(qty))
        for item_id, qty in inventory
        if (int(item_id) & 0xFF) == int(ammo_id)
    )


def can_fire_weapon(
    inventory: list[tuple[int, int]],
    weapon_id: int,
) -> bool:
    """True when weapon + reserve piles can supply at least one round (COMBINE gating)."""
    return total_fireable_ammo(inventory, weapon_id) > 0


def can_fire_from_equipped_slot(
    inventory: list[tuple[int, int]],
    weapon_id: int,
    equipped_slot_0based: int | None = None,
) -> bool:
    """True when the equipped weapon slot has loaded ammo (attack gating)."""
    return loaded_weapon_ammo(inventory, weapon_id, equipped_slot_0based) > 0


_NAME_TO_ITEM_ID = {name: iid for iid, name in ITEM_IDS.items()}


def inventory_slots_to_id_qty(
    inventory_slots: list[Any] | None,
) -> list[tuple[int, int]]:
    """Normalize ``inventory_slots`` to ``[(item_id, qty), ...]``."""
    out: list[tuple[int, int]] = []
    for entry in inventory_slots or []:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            raw_id, qty = entry[0], int(entry[1])
            if isinstance(raw_id, str):
                iid = int(_NAME_TO_ITEM_ID.get(canonical_item(raw_id), 0))
            else:
                iid = int(raw_id) & 0xFF
            out.append((iid, qty if iid else 0))
        elif isinstance(entry, dict):
            name = canonical_item(str(entry.get("name") or entry.get("item") or ""))
            iid = int(_NAME_TO_ITEM_ID.get(name, 0))
            qty = int(entry.get("qty", 1) or 0)
            out.append((iid, qty if iid else 0))
    return out


def fireable_ammo_before_miss(
    state: dict[str, Any] | None,
    weapon_id: int,
    *,
    rounds_spent: int = 0,
) -> int | None:
    """Total fireable ammo before a miss, or None when inventory is unknown."""
    wid = int(weapon_id) & 0xFF
    if wid == 0x01:
        return 1
    slots = (state or {}).get("inventory_slots")
    if slots is None:
        return None
    inv = inventory_slots_to_id_qty(slots)
    after = total_fireable_ammo(inv, wid)
    spent = max(0, int(rounds_spent))
    if spent > 0:
        return max(after + spent, 1)
    return after
