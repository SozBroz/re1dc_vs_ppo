"""Validate Jill dining-room spawn for curriculum reset (unbeaten / fresh start)."""

from __future__ import annotations

from re1_rl.memory_map import (
    GAME_MODE,
    IN_CONTROL_MASK,
    ITEM_IDS,
    MENU_ROOM_ID,
    decode_inventory,
)

DINING_STAGE = 0
DINING_ROOM = 5  # mansion stage 0 room 5 == dining 105
JILL_ID = 1

# Items that should not appear before gallery / mid-game on an unbeaten run.
FORBIDDEN_ITEM_IDS: frozenset[int] = frozenset(
    {
        0x38,  # special_key
        0x2D,  # star_crest
        0x2C,  # moon_crest
        0x2E,  # sun_crest
        0x29,  # wind_crest
        0x0A,  # rocket_launcher
        0x33,
        0x34,
        0x35,
        0x36,  # mansion keys
        0x37,
        0x3B,
        0x3C,  # lab keys
        0x3D,  # small_key
    }
)

# Fresh Jill after the dining intro (knife + beretta + one spray).
# Chris also starts with knife + beretta — character_id is the only reliable gate.
REQUIRED_STARTER_IDS: frozenset[int] = frozenset({0x01, 0x02})

def inventory_item_ids(ram: dict) -> set[int]:
    ids: set[int] = set()
    for name, _qty in decode_inventory(ram):
        for iid, label in ITEM_IDS.items():
            if label == name:
                ids.add(iid)
                break
    return ids


def validate_fresh_dining_spawn(
    ram: dict,
    *,
    require_control: bool = True,
    require_starters: bool = True,
    require_jill: bool = True,
) -> tuple[bool, list[str]]:
    """Return (ok, human-readable failure reasons)."""
    errors: list[str] = []

    if require_jill and int(ram.get("character_id", -1)) != JILL_ID:
        errors.append(f"character_id={ram.get('character_id')} (want Jill={JILL_ID})")

    if int(ram.get("stage_id", -1)) != DINING_STAGE:
        errors.append(f"stage_id={ram.get('stage_id')} (want {DINING_STAGE})")

    if int(ram.get("room_id", -1)) != DINING_ROOM:
        errors.append(f"room_id={ram.get('room_id')} (want dining {DINING_ROOM} / 105)")

    hp = int(ram.get("player_hp", 0))
    if hp <= 0 or hp > 140:
        errors.append(f"player_hp={hp} (want 1..140)")

    mode = int(ram.get("game_mode", 0))
    in_control = bool(mode & IN_CONTROL_MASK)
    if require_control and not in_control:
        errors.append(f"game_mode=0x{mode:02X} (not in player control)")

    inv = decode_inventory(ram)
    ids = inventory_item_ids(ram)
    for iid in sorted(ids & FORBIDDEN_ITEM_IDS):
        errors.append(f"forbidden item {ITEM_IDS.get(iid, hex(iid))}")

    if require_starters and not REQUIRED_STARTER_IDS.issubset(ids):
        missing = REQUIRED_STARTER_IDS - ids
        names = [ITEM_IDS.get(i, hex(i)) for i in sorted(missing)]
        errors.append(f"missing starter items: {names}")

    if len(inv) > 6:
        errors.append(f"inventory too large for fresh start ({len(inv)} slots): {inv}")

    return (len(errors) == 0, errors)


def format_spawn_summary(ram: dict) -> str:
    inv = decode_inventory(ram)
    mode = int(ram.get("game_mode", 0))
    return (
        f"hp={ram.get('player_hp')} char={ram.get('character_id')} "
        f"stage={ram.get('stage_id')} room={ram.get('room_id')} "
        f"mode=0x{mode:02X} control={bool(mode & IN_CONTROL_MASK)} "
        f"inv={inv}"
    )
