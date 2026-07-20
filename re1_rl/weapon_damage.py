"""Nominal weapon damage, ammo-qty norm, and boss-room bonus flags.

Shared ammo scale (``AMMO_QTY_NORM``) is used by inventory qty, box qty,
``weapon_card.equipped_clip``, and ``last_attack`` clip/spent fields so the
policy can relate clip ↔ spend ↔ inventory on one axis. Do not reintroduce ``/15``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Shared qty / clip / ammo_spent normalization for inventory, box, weapon_card,
# and last_attack. Clipped to [0, 1] after divide.
AMMO_QTY_NORM = 255.0

DMG_NORM = 255.0
KILLS_NORM = 5.0
MAX_LAST_ATTACK_EVENTS = 2
MAX_ENEMY_TYPE = 32.0

# Nominal damage per equipped weapon id (PS1 DC). Flamethrower unverified → 0.
# Shotgun uses (min, max); others use equal min/max.
WEAPON_NOMINAL_DAMAGE: dict[int, tuple[int, int]] = {
    0x01: (2, 2),       # combat knife
    0x02: (4, 4),       # beretta
    0x03: (15, 25),     # shotgun (range-scaled)
    0x04: (100, 100),   # colt python dumdum
    0x05: (100, 100),   # colt python magnum
    0x06: (0, 0),       # flamethrower — unverified
    0x07: (60, 60),     # bazooka acid
    0x08: (35, 35),     # bazooka explosive
    0x09: (50, 50),     # bazooka flame
    0x0A: (35, 35),     # rocket launcher (explosive family)
}

SHOTGUN_RANGE_SCALED_IDS: frozenset[int] = frozenset({0x03})

# Round-type one-hot order: none, acid, flame, explosive
ROUND_TYPE_NONE = 0
ROUND_TYPE_ACID = 1
ROUND_TYPE_FLAME = 2
ROUND_TYPE_EXPLOSIVE = 3
ROUND_TYPE_NAMES: tuple[str, ...] = ("none", "acid", "flame", "explosive")

WEAPON_ROUND_TYPE: dict[int, int] = {
    0x07: ROUND_TYPE_ACID,
    0x09: ROUND_TYPE_FLAME,
    0x08: ROUND_TYPE_EXPLOSIVE,
    0x0A: ROUND_TYPE_EXPLOSIVE,
}

# Boss rooms by round matchup (no boss entity ids — room proxy only).
ACID_BONUS_ROOMS: frozenset[str] = frozenset(
    {
        "210",  # ATTIC (Yawn)
        "20C",  # LESSON ROOM (Yawn upstairs)
        "513",  # TYRANT ROOM
        "514",  # FRONT OF TYRANT
    }
)
FLAME_BONUS_ROOMS: frozenset[str] = frozenset(
    {
        "40C",  # PLANT BOSS ROOM (Plant 42)
        "30C",  # BLACK TIGER ROOM
    }
)

WEAPON_CARD_FIELDS: list[tuple[str, str]] = [
    ("equipped_clip", "loaded rounds in equipped weapon / AMMO_QTY_NORM"),
    ("dmg_min", "nominal damage min / 255"),
    ("dmg_max", "nominal damage max / 255"),
    ("range_scaled", "1 = shotgun range-scaled damage band"),
    ("round_none", "round-type one-hot: none"),
    ("round_acid", "round-type one-hot: acid"),
    ("round_flame", "round-type one-hot: flame"),
    ("round_explosive", "round-type one-hot: explosive"),
    ("in_acid_bonus_room", "1 = current room in acid bonus set"),
    ("in_flame_bonus_room", "1 = current room in flame bonus set"),
    ("acid_bonus_active", "1 = acid room AND acid rounds equipped"),
    ("flame_bonus_active", "1 = flame room AND flame rounds equipped"),
]
WEAPON_CARD_DIM = len(WEAPON_CARD_FIELDS)  # 12

# Combat height one-hot inside last_attack (all 0 when valid=0).
# Weapon identity is equipped_weapon / weapon_card — not duplicated here.
# Legacy knife_swing (crouch knife) maps to attack_down.
LAST_ATTACK_MACRO_NEUTRAL = 0
LAST_ATTACK_MACRO_UP = 1
LAST_ATTACK_MACRO_DOWN = 2
LAST_ATTACK_MACRO_NAMES: tuple[str, ...] = (
    "attack_neutral",
    "attack_up",
    "attack_down",
)
N_LAST_ATTACK_MACROS = len(LAST_ATTACK_MACRO_NAMES)  # 3

LAST_ATTACK_FIELDS: list[tuple[str, str]] = [
    ("valid", "1 = filled this step from knife/attack macro"),
    ("hit", "1 = dealt damage or kill this attack"),
    ("clip_before", "equipped clip before attack / AMMO_QTY_NORM"),
    ("clip_after", "equipped clip after attack / AMMO_QTY_NORM"),
    ("ammo_spent", "rounds spent / AMMO_QTY_NORM"),
    ("total_damage", "sum enemy HP lost / 255"),
    ("kills", "enemies killed this attack / 5"),
    ("event0_hp_before", "event 0 HP before / 255"),
    ("event0_hp_after", "event 0 HP after / 255"),
    ("event0_type_id", "event 0 enemy type_id / 32"),
    ("event1_hp_before", "event 1 HP before / 255"),
    ("event1_hp_after", "event 1 HP after / 255"),
    ("event1_type_id", "event 1 enemy type_id / 32"),
    ("macro_attack_neutral", "1 = last combat height was attack (standing)"),
    ("macro_attack_up", "1 = last combat height was attack_up"),
    ("macro_attack_down", "1 = last combat height was attack_down (incl. knife_swing)"),
]
LAST_ATTACK_DIM = len(LAST_ATTACK_FIELDS)  # 16
LAST_ATTACK_MACRO_OFFSET = 13  # index of height one-hot start


def ammo_qty_norm(qty: int | float) -> float:
    """Normalize a raw ammo/clip count onto the shared [0, 1] scale."""
    return float(np.clip(float(qty) / AMMO_QTY_NORM, 0.0, 1.0))


def weapon_round_type(weapon_id: int) -> int:
    return int(WEAPON_ROUND_TYPE.get(int(weapon_id) & 0xFF, ROUND_TYPE_NONE))


def nominal_damage_range(weapon_id: int) -> tuple[int, int]:
    return WEAPON_NOMINAL_DAMAGE.get(int(weapon_id) & 0xFF, (0, 0))


def room_bonus_flags(room_id: str | None, weapon_id: int) -> dict[str, float]:
    """Acid/flame room membership and active matchup vs equipped round."""
    room = str(room_id or "")
    wid = int(weapon_id) & 0xFF
    in_acid = 1.0 if room in ACID_BONUS_ROOMS else 0.0
    in_flame = 1.0 if room in FLAME_BONUS_ROOMS else 0.0
    rtype = weapon_round_type(wid)
    return {
        "in_acid_bonus_room": in_acid,
        "in_flame_bonus_room": in_flame,
        "acid_bonus_active": 1.0 if in_acid and rtype == ROUND_TYPE_ACID else 0.0,
        "flame_bonus_active": 1.0 if in_flame and rtype == ROUND_TYPE_FLAME else 0.0,
    }


def encode_weapon_card(
    *,
    weapon_id: int,
    equipped_clip: int,
    room_id: str | None,
) -> np.ndarray:
    """Always-on weapon card: clip, nominal dmg, round type, room bonuses."""
    v = np.zeros(WEAPON_CARD_DIM, dtype=np.float32)
    wid = int(weapon_id) & 0xFF
    dmg_min, dmg_max = nominal_damage_range(wid)
    rtype = weapon_round_type(wid)
    flags = room_bonus_flags(room_id, wid)
    v[0] = ammo_qty_norm(equipped_clip)
    v[1] = float(np.clip(dmg_min / DMG_NORM, 0.0, 1.0))
    v[2] = float(np.clip(dmg_max / DMG_NORM, 0.0, 1.0))
    v[3] = 1.0 if wid in SHOTGUN_RANGE_SCALED_IDS else 0.0
    v[4 + rtype] = 1.0
    v[8] = flags["in_acid_bonus_room"]
    v[9] = flags["in_flame_bonus_room"]
    v[10] = flags["acid_bonus_active"]
    v[11] = flags["flame_bonus_active"]
    return v


def empty_last_attack() -> np.ndarray:
    return np.zeros(LAST_ATTACK_DIM, dtype=np.float32)


def last_attack_macro_from_action(action_id: int) -> int | None:
    """Map discrete combat action id → last_attack height one-hot index, else None.

    ``knife_swing`` maps to down (crouch path). Weapon is read from equip, not here.
    """
    from re1_rl.action_mask import (
        ATTACK_ACTION,
        ATTACK_DOWN_ACTION,
        ATTACK_UP_ACTION,
        KNIFE_SWING_ACTION,
    )

    aid = int(action_id)
    if aid == ATTACK_ACTION:
        return LAST_ATTACK_MACRO_NEUTRAL
    if aid == ATTACK_UP_ACTION:
        return LAST_ATTACK_MACRO_UP
    if aid in (ATTACK_DOWN_ACTION, KNIFE_SWING_ACTION):
        return LAST_ATTACK_MACRO_DOWN
    return None


def _enemy_type_by_slot(enemies: list[dict[str, Any]] | None) -> dict[int, int]:
    out: dict[int, int] = {}
    for ent in enemies or []:
        slot = int(ent.get("slot", -1))
        if slot < 0:
            continue
        tid = ent.get("type_id", ent.get("model_id", 0))
        out[slot] = int(tid or 0)
    return out


def pack_last_attack(
    *,
    knife: bool,
    attack: bool,
    combat_events: list[dict[str, Any]] | None,
    enemy_damage: int,
    enemy_kills: int,
    clip_before: int,
    clip_after: int,
    ammo_spent: int,
    enemies_before: list[dict[str, Any]] | None = None,
    action_id: int | None = None,
    attack_macro: int | None = None,
) -> np.ndarray:
    """Pack one-step last_attack memory (same combat gates as apply_combat_step_fields).

    Call only when knife/attack ran and room did not change (caller already gated).
    Knife / non-ammo: clip fields stay 0.
    Height one-hot: ``attack_macro`` (0..2) or derived from ``action_id``; if both
    omitted, infer knife→down / attack→neutral. Weapon comes from equip obs.
    """
    v = empty_last_attack()
    if not knife and not attack:
        return v
    damage = int(enemy_damage)
    kills = int(enemy_kills)
    events = list(combat_events or [])
    hit = 1.0 if (damage > 0 or kills > 0) else 0.0
    v[0] = 1.0
    v[1] = hit
    if knife:
        v[2] = 0.0
        v[3] = 0.0
        v[4] = 0.0
    else:
        v[2] = ammo_qty_norm(clip_before)
        v[3] = ammo_qty_norm(clip_after)
        v[4] = ammo_qty_norm(ammo_spent)
    v[5] = float(np.clip(damage / DMG_NORM, 0.0, 1.0))
    v[6] = float(np.clip(kills / KILLS_NORM, 0.0, 1.0))
    type_by_slot = _enemy_type_by_slot(enemies_before)
    for i, ev in enumerate(events[:MAX_LAST_ATTACK_EVENTS]):
        base = 7 + i * 3
        v[base] = float(np.clip(int(ev.get("hp_before", 0)) / DMG_NORM, 0.0, 1.0))
        v[base + 1] = float(np.clip(int(ev.get("hp_after", 0)) / DMG_NORM, 0.0, 1.0))
        slot = int(ev.get("slot", -1))
        tid = int(ev.get("type_id", type_by_slot.get(slot, 0)))
        v[base + 2] = float(np.clip(tid / MAX_ENEMY_TYPE, 0.0, 1.0))
    macro = attack_macro
    if macro is None and action_id is not None:
        macro = last_attack_macro_from_action(int(action_id))
    if macro is None:
        if knife:
            macro = LAST_ATTACK_MACRO_DOWN
        elif attack:
            macro = LAST_ATTACK_MACRO_NEUTRAL
    if macro is not None and 0 <= int(macro) < N_LAST_ATTACK_MACROS:
        v[LAST_ATTACK_MACRO_OFFSET + int(macro)] = 1.0
    return v


def equipped_clip_from_inventory_slots(
    inventory_slots: list[tuple[str, int]] | list[tuple[int, int]] | None,
    weapon_id: int,
) -> int:
    """Loaded rounds in the equipped weapon from name- or id-keyed slots.

    Knife and unknown weapons → 0 (knife has no ammo clip in obs).
    """
    from re1_rl.memory_map import ITEM_IDS
    from re1_rl.item_todo import canonical_item

    wid = int(weapon_id) & 0xFF
    if wid in (0, 0x01):
        return 0
    name_to_id = {name: iid for iid, name in ITEM_IDS.items()}
    for slot in inventory_slots or []:
        if not slot or len(slot) < 2:
            continue
        raw_id, qty = slot[0], slot[1]
        if isinstance(raw_id, str):
            iid = int(name_to_id.get(canonical_item(raw_id), 0))
        else:
            iid = int(raw_id) & 0xFF
        if iid == wid:
            return max(0, int(qty))
    return 0
