"""Ammo totals for RE1 PS1 Director's Cut attack / combine gating."""

from __future__ import annotations

from re1_rl.memory_map import WEAPON_ITEM_IDS

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


def can_fire_weapon(
    inventory: list[tuple[int, int]],
    weapon_id: int,
) -> bool:
    return total_fireable_ammo(inventory, weapon_id) > 0
