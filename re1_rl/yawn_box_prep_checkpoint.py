"""Yawn prep save room (118): deposit wind crest; box must have no guns/ammo."""

from __future__ import annotations

from typing import Any

from re1_rl.item_box import (
    BOX_SLOTS,
    BOX_SLOTS_LIVE,
    DEPOSIT_ITEM_ALLOWLIST,
    KNIFE_ITEM_ID,
)
from re1_rl.item_todo import canonical_item

WIND_CREST_ITEM_ID = 0x29
YAWN_BOX_PREP_CHECKPOINT_ID = "yawn_box_prep_118"
YAWN_BOX_PREP_ROOM = "118"
WIND_CREST_NAME = "wind_crest"


def wind_crest_deposit_allowed(item_id: int, room_id: str | None) -> bool:
    """Room 118 storeroom may bank the wind crest before Yawn."""
    return (
        str(room_id or "").strip().upper() == YAWN_BOX_PREP_ROOM
        and int(item_id) & 0xFF == WIND_CREST_ITEM_ID
    )


def yawn_box_forbidden_weapon_ammo_ids() -> frozenset[int]:
    """Weapons except knife, plus all ammo pack ids."""
    from re1_rl.item_box import BAZOOKA_AMMO_IDS
    from re1_rl.memory_map import WEAPON_ITEM_IDS

    ammo = frozenset({0x0B, 0x0C, 0x0D, 0x0E, 0x0F}) | BAZOOKA_AMMO_IDS
    weapons = WEAPON_ITEM_IDS - {KNIFE_ITEM_ID}
    return weapons | ammo


def box_pairs_from_state(state: dict[str, Any]) -> list[tuple[int, int]]:
    raw = state.get("box_cache")
    if not isinstance(raw, list):
        return []
    pairs: list[tuple[int, int]] = []
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            try:
                pairs.append((int(entry[0]), int(entry[1])))
            except (TypeError, ValueError):
                continue
    return pairs


def box_has_item(
    box: list[tuple[int, int]] | None,
    item_name: str,
    *,
    max_slot: int = BOX_SLOTS_LIVE - 1,
) -> bool:
    from re1_rl.key_items import KEY_ITEM_NAMES
    from re1_rl.memory_map import ITEM_IDS

    want = canonical_item(item_name)
    if not want or not box:
        return False
    limit = max(0, min(int(max_slot), len(box) - 1))
    for i in range(0, limit + 1):
        entry = box[i]
        if not entry:
            continue
        item_id = int(entry[0]) & 0xFF
        if item_id == 0:
            continue
        name = canonical_item(ITEM_IDS.get(item_id, "") or "")
        if want in KEY_ITEM_NAMES:
            if name == want:
                return True
        elif name == want:
            return True
    return False


def yawn_box_weapon_ammo_clear(box: list[tuple[int, int]] | None) -> bool:
    """True when modeled + deep box slots contain no guns or ammo (knife ok)."""
    if not box:
        return True
    forbidden = yawn_box_forbidden_weapon_ammo_ids()
    for entry in box:
        if not entry:
            continue
        item_id = int(entry[0]) & 0xFF
        if item_id == 0:
            continue
        if item_id in forbidden:
            return False
    return True


def yawn_box_prep_box_pollution_reason(
    box: list[tuple[int, int]] | None,
) -> str | None:
    """Stricter than generic pollution: wind crest ok; no guns/ammo; knife/heals ok."""
    from re1_rl.key_items import KEY_ITEM_NAMES
    from re1_rl.memory_map import ITEM_IDS

    if not box:
        return None

    forbidden = yawn_box_forbidden_weapon_ammo_ids()
    key_names = frozenset(KEY_ITEM_NAMES)
    allowed_modeled = DEPOSIT_ITEM_ALLOWLIST | {WIND_CREST_ITEM_ID}

    for i, entry in enumerate(box):
        if not entry:
            continue
        item_id = int(entry[0]) & 0xFF
        if item_id == 0:
            continue
        name = canonical_item(ITEM_IDS.get(item_id, "") or "")

        if item_id in forbidden:
            label = name or f"0x{item_id:02x}"
            return f"yawn_box_weapon_ammo:{label}@{i}"

        if name and name in key_names and name != WIND_CREST_NAME:
            return f"key_item_in_box:{name}@{i}"

        if i >= BOX_SLOTS:
            if name == WIND_CREST_NAME:
                continue
            if item_id in allowed_modeled:
                continue
            label = name or f"0x{item_id:02x}"
            return f"deep_box_item:{label}@{i}"

        if item_id not in allowed_modeled:
            label = name or f"0x{item_id:02x}"
            return f"disallowed_item_in_box:{label}@{i}"

    return None


def yawn_box_prep_capture_ready(
    box: list[tuple[int, int]] | None,
    inventory_names: list[str] | tuple[str, ...] | None,
) -> str | None:
    """Return failure reason when capture preconditions are not met."""
    pollution = yawn_box_prep_box_pollution_reason(box)
    if pollution:
        return pollution
    if not box_has_item(box, WIND_CREST_NAME):
        return "wind_crest_not_in_box"
    inv = {canonical_item(str(x)) for x in (inventory_names or []) if str(x).strip()}
    if WIND_CREST_NAME in inv:
        return "wind_crest_still_held"
    return None
