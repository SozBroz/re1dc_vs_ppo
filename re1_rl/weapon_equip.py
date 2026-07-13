"""Magic weapon equip via RAM writes (no pause-menu navigation).

Verified live (collect_weapon_frame_data.py slot-drain probe, 2026-07-07):
write the equipped item id (0x800C5126), the 1-BASED slot index
(0x800C8689 — firing drains ammo from slot index-1), and the 0-based slot
byte (0x800C50BE). The engine then aims/fires the weapon and consumes the
correct slot's ammo.
"""

from __future__ import annotations

from typing import Any

from re1_rl.memory_map import (
    EQUIPPED_SLOT_INDEX,
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    INVENTORY_BASE,
    INVENTORY_SLOTS,
    ITEM_IDS,
    WEAPON_ITEM_IDS,
)

# PS1 arsenal (excludes PC-only bonus guns).
EQUIPPABLE_WEAPON_IDS: tuple[int, ...] = (
    0x01,  # knife
    0x02,  # beretta
    0x03,  # shotgun
    0x04,  # colt_python_dumdum
    0x05,  # colt_python
    0x06,  # flamethrower
    0x07,  # bazooka_acid
    0x08,  # bazooka_explosive
    0x09,  # bazooka_flame
    0x0A,  # rocket_launcher
)

# Jill start (and often forever): knife slot shows item_id=0x01, qty=0 in RAM.
# The knife is still equippable; policy/mask treat it as always owned.
KNIFE_ITEM_ID = 0x01
POLICY_KNIFE_QTY = 99


def policy_item_qty(item_id: int, qty: int) -> int:
    """Quantity for masks/obs only — never written back to RAM."""
    if int(item_id) == KNIFE_ITEM_ID and int(qty) <= 0:
        return POLICY_KNIFE_QTY
    return int(qty)


def policy_inventory(
    inventory: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Inventory copy with knife qty normalized for the policy."""
    return [(int(iid), policy_item_qty(iid, qty)) for iid, qty in inventory]


def read_inventory_ids(bridge: Any) -> list[int]:
    """Item id per inventory slot (0 = empty)."""
    fields = [
        (f"inv_slot_{i}", INVENTORY_BASE + 2 * i, "u16")
        for i in range(INVENTORY_SLOTS)
    ]
    ram = bridge.read_ram(fields)
    return [int(ram.get(f"inv_slot_{i}", 0)) & 0xFF for i in range(INVENTORY_SLOTS)]


def _armed(
    equipped_weapon_id: int | None,
    equipped_slot_0based: int | None = None,
) -> bool:
    if equipped_weapon_id is not None and int(equipped_weapon_id) in EQUIPPABLE_WEAPON_IDS:
        return True
    return equipped_slot_0based is not None and int(equipped_slot_0based) >= 0


def _explicitly_unarmed(
    equipped_weapon_id: int | None,
    equipped_slot_0based: int | None = None,
) -> bool:
    """True only when RAM says hands are empty (unknown reads -> not legal)."""
    if equipped_weapon_id is None:
        return False
    if int(equipped_weapon_id) != 0:
        return False
    return not (
        equipped_slot_0based is not None and int(equipped_slot_0based) >= 0
    )


def read_equipped_slot_0based(bridge: Any) -> int | None:
    """0-based inventory slot of the equipped weapon, or None if unarmed."""
    ram = bridge.read_ram(
        [("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8")]
    )
    slot_1b = int(ram.get("equipped_slot_1based", 0))
    return slot_1b - 1 if slot_1b > 0 else None


def weapon_already_equipped(
    equipped_weapon_id: int | None,
    item_id: int,
) -> bool:
    """True when ``item_id`` is already the held weapon (EQUIP would toggle off)."""
    return (
        equipped_weapon_id is not None
        and int(equipped_weapon_id) != 0
        and int(item_id) == int(equipped_weapon_id)
    )


def slot_legal_for_equip(
    inventory: list[tuple[int, int]],
    slot: int,
    *,
    equipped_weapon_id: int | None,
    equipped_slot_0based: int | None,
) -> bool:
    """Legal equip: equippable weapon in slot, not already held.

    Empty guns (qty 0) stay equippable — ammo may sit in a spare pile
    (e.g. handgun_bullets 0x0B) and get COMBINE-loaded afterward. Knife RAM
    qty 0 is still a real item.
    """
    del equipped_slot_0based
    if slot < 0 or slot >= len(inventory):
        return False
    item_id, _qty = inventory[slot]
    if int(item_id) not in EQUIPPABLE_WEAPON_IDS:
        return False
    return not weapon_already_equipped(equipped_weapon_id, int(item_id))


def any_legal_equip_slot(
    inventory: list[tuple[int, int]],
    *,
    equipped_weapon_id: int | None,
    equipped_slot_0based: int | None,
) -> bool:
    """True when inventory has any weapon the agent could switch to."""
    for i in range(len(inventory)):
        if slot_legal_for_equip(
            inventory,
            i,
            equipped_weapon_id=equipped_weapon_id,
            equipped_slot_0based=equipped_slot_0based,
        ):
            return True
    return False


def can_equip(weapon_id: int, *, equipped_id: int, inventory_ids: list[int]) -> bool:
    """Legal iff the weapon is in inventory and not already equipped."""
    if weapon_id not in WEAPON_ITEM_IDS:
        return False
    if weapon_id == equipped_id:
        return False
    return weapon_id in inventory_ids


def magic_equip_slot(bridge: Any, slot: int) -> dict[str, Any]:
    """Equip the weapon in ``slot`` by writing RAM mirrors."""
    inv = read_inventory_ids(bridge)
    if slot < 0 or slot >= len(inv):
        return {"ok": False, "reason": "bad_slot", "weapon": None, "slot": slot}
    weapon_id = int(inv[slot])
    if weapon_id not in WEAPON_ITEM_IDS or weapon_id == 0:
        return {
            "ok": False,
            "reason": "not_a_weapon",
            "weapon": ITEM_IDS.get(weapon_id),
            "slot": slot,
        }
    bridge.write_ram([
        ("equipped_id", EQUIPPED_WEAPON_ID, "u8", weapon_id),
        ("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8", int(slot) + 1),
        ("equipped_slot", EQUIPPED_SLOT_INDEX, "u8", int(slot)),
    ])
    return {
        "ok": True,
        "reason": "",
        "weapon": ITEM_IDS.get(weapon_id),
        "slot": int(slot),
    }


def magic_equip(bridge: Any, weapon_id: int) -> dict[str, Any]:
    """Equip first inventory slot holding ``weapon_id`` (legacy helper)."""
    inv = read_inventory_ids(bridge)
    if weapon_id not in inv:
        return {
            "ok": False,
            "reason": "not_in_inventory",
            "weapon": ITEM_IDS.get(weapon_id),
        }
    slot = inv.index(weapon_id)
    return magic_equip_slot(bridge, slot)
