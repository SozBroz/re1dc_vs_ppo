"""RE1 DC inventory USE (consumable heal / cure poison).

PS1 manual: highlight item -> USE (weapons show EQUIP instead).
Effects from SparkyCoulter RE1 DC item guide + Jacko herb chart.
Heal USE mask uses Jill Fine HP (JILL_FINE_HP=96), not RAM ceiling 140.
"""

from __future__ import annotations

from typing import Any

from re1_rl.memory_map import (
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    INVENTORY_BASE,
    PLAYER_HP,
    PLAYER_HP_MAX,
)
from re1_rl.reward import JILL_FINE_HP

# Heal USE legal at or below this fraction of Jill Fine/max HP.
HEAL_USE_HP_FRACTION = 0.70

# item_id -> (heal_hp, cures_poison). None = cannot USE (e.g. red herb alone).
_USE_EFFECTS: dict[int, tuple[int, bool] | None] = {
    0x41: (PLAYER_HP_MAX, False),  # first_aid_spray (PS1 id; 0x0B is handgun_bullets)
    0x42: (PLAYER_HP_MAX, True),   # serum — full + poison (story / lab)
    0x43: None,  # red herb alone
    0x44: (25, False),  # green
    0x45: (0, True),  # blue — poison only
    0x46: (70, False),  # G+R
    0x47: (50, False),  # G+G
    0x48: (25, True),  # G+B
    0x49: (PLAYER_HP_MAX, True),  # G+R+B
    0x4A: (PLAYER_HP_MAX, False),  # G+G+G
    0x4B: (50, True),  # G+G+B
}

USABLE_ITEM_IDS = frozenset(
    iid for iid, eff in _USE_EFFECTS.items() if eff is not None
)


def use_effect(item_id: int) -> tuple[int, bool] | None:
    return _USE_EFFECTS.get(int(item_id) & 0xFF)


def is_usable_item(item_id: int) -> bool:
    return use_effect(item_id) is not None


def heal_use_hp_threshold(max_hp: int = JILL_FINE_HP) -> float:
    """Inclusive ceiling: heal USE legal when ``hp <=`` this value."""
    return HEAL_USE_HP_FRACTION * float(max_hp)


def use_would_help(
    item_id: int,
    *,
    current_hp: int,
    poisoned: bool = False,
    episode_start_hp: int | None = None,
) -> bool:
    """True when USE would heal (HP ≤ 70% Fine) or cure poison (any HP).

    ``episode_start_hp`` is accepted for call-site compatibility; heal gating
    uses ``JILL_FINE_HP`` (96), not the RAM ceiling 140.
    """
    del episode_start_hp  # mask uses JILL_FINE_HP; kept for API parity
    effect = use_effect(item_id)
    if effect is None:
        return False
    heal_amt, cures_poison = effect
    if cures_poison and poisoned:
        return True
    if int(heal_amt) <= 0:
        return False
    return int(current_hp) <= heal_use_hp_threshold(JILL_FINE_HP)


def slot_legal_for_use(
    inventory: list[tuple[int, int]],
    slot: int,
    *,
    current_hp: int | None = None,
    poisoned: bool = False,
    episode_start_hp: int | None = None,
) -> bool:
    if current_hp is None:
        return False
    if slot < 0 or slot >= len(inventory):
        return False
    item_id, qty = inventory[slot]
    if int(item_id) == 0 or int(qty) <= 0:
        return False
    return use_would_help(
        int(item_id),
        current_hp=int(current_hp),
        poisoned=bool(poisoned),
        episode_start_hp=episode_start_hp,
    )


def any_legal_use_slot(
    inventory: list[tuple[int, int]],
    *,
    current_hp: int | None = None,
    poisoned: bool = False,
    episode_start_hp: int | None = None,
) -> bool:
    if current_hp is None:
        return False
    return any(
        slot_legal_for_use(
            inventory,
            i,
            current_hp=current_hp,
            poisoned=poisoned,
            episode_start_hp=episode_start_hp,
        )
        for i in range(len(inventory))
    )


def plan_use(
    inventory: list[tuple[int, int]],
    slot: int,
    *,
    current_hp: int,
    poisoned: bool = False,
    episode_start_hp: int | None = None,
) -> tuple[list[tuple[int, int]], int, bool, int] | None:
    """Return ``(new_inv, heal_applied, cured_poison, item_id)``."""
    if not slot_legal_for_use(
        inventory,
        slot,
        current_hp=current_hp,
        poisoned=poisoned,
        episode_start_hp=episode_start_hp,
    ):
        return None
    item_id, _qty = inventory[slot]
    effect = use_effect(item_id)
    assert effect is not None
    heal_amt, cures_poison = effect
    new_hp = min(PLAYER_HP_MAX, max(0, int(current_hp)) + int(heal_amt))
    heal_applied = new_hp - max(0, int(current_hp))

    new_inv = list(inventory)
    new_inv[slot] = (0, 0)
    return new_inv, heal_applied, bool(cures_poison), int(item_id)


def apply_use(bridge: Any, slot: int) -> dict[str, Any]:
    from re1_rl.item_box import _slot_write_fields, read_inventory

    inventory = read_inventory(bridge)
    ram = bridge.read_ram([("player_hp", PLAYER_HP, "u16")])
    current_hp = int(ram.get("player_hp", 0))
    planned = plan_use(inventory, slot, current_hp=current_hp)
    if planned is None:
        return {
            "ok": False,
            "reason": "not_usable",
            "item_id": None,
            "heal_applied": 0,
            "cured_poison": False,
        }

    new_inv, heal_applied, cured_poison, item_id = planned
    new_hp = min(PLAYER_HP_MAX, current_hp + heal_applied)
    fields: list[tuple[str, int, str, int]] = [
        ("player_hp", PLAYER_HP, "u16", new_hp),
        *_slot_write_fields("inv", INVENTORY_BASE, new_inv),
    ]

    unequipped = False
    ram_eq = bridge.read_ram(
        [
            ("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8"),
            ("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8"),
        ]
    )
    slot_1b = int(ram_eq.get("equipped_slot_1based", 0))
    if slot_1b > 0 and (slot_1b - 1) == int(slot):
        if inventory[slot][0] == int(ram_eq.get("equipped_weapon_id", 0)):
            unequipped = int(ram_eq.get("equipped_weapon_id", 0)) != 0
    if unequipped:
        fields.append(("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8", 0))
        fields.append(("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8", 0))

    bridge.write_ram(fields)
    return {
        "ok": True,
        "reason": "",
        "item_id": item_id,
        "heal_applied": heal_applied,
        "hp_after": new_hp,
        "cured_poison": cured_poison,
        "unequipped": unequipped,
    }
