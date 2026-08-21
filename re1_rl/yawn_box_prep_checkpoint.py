"""Yawn prep save room (118): bank wind crest + armor key; box must have no guns/ammo."""

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
ARMOR_KEY_ITEM_ID = 0x34
YAWN_BOX_PREP_CHECKPOINT_ID = "yawn_box_prep_118"
YAWN_BOX_PREP_ROOM = "118"
YAWN_BOX_PREP_EXIT_ROOM = "10B"
WIND_CREST_NAME = "wind_crest"
ARMOR_KEY_NAME = "armor_key"
YAWN_BOX_PREP_BANKED_KEYS = (WIND_CREST_NAME, ARMOR_KEY_NAME)
YAWN_BOX_PREP_BANKED_KEY_IDS = frozenset({WIND_CREST_ITEM_ID, ARMOR_KEY_ITEM_ID})
YAWN_BOX_PREP_HELD_KEYS = ("shield_key",)
YAWN_BOX_PREP_HELD_FIREPOWER = (
    "beretta",
    "shotgun",
    "bazooka_acid",
    "handgun_bullets",
    "shotgun_shells",
    "acid_rounds",
)


def yawn_118_key_deposit_allowed(item_id: int, room_id: str | None) -> bool:
    """Room 118 may bank the wind crest and armor key before Yawn."""
    return (
        str(room_id or "").strip().upper() == YAWN_BOX_PREP_ROOM
        and int(item_id) & 0xFF in YAWN_BOX_PREP_BANKED_KEY_IDS
    )


def wind_crest_deposit_allowed(item_id: int, room_id: str | None) -> bool:
    """Room 118 storeroom may bank the wind crest (and armor key) before Yawn."""
    return yawn_118_key_deposit_allowed(item_id, room_id)


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
    """Stricter than generic pollution: crest/armor key ok; no guns/ammo; knife/heals ok."""
    from re1_rl.key_items import KEY_ITEM_NAMES
    from re1_rl.memory_map import ITEM_IDS

    if not box:
        return None

    forbidden = yawn_box_forbidden_weapon_ammo_ids()
    key_names = frozenset(KEY_ITEM_NAMES)
    allowed_modeled = DEPOSIT_ITEM_ALLOWLIST | YAWN_BOX_PREP_BANKED_KEY_IDS

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

        if name and name in key_names and name not in YAWN_BOX_PREP_BANKED_KEYS:
            return f"key_item_in_box:{name}@{i}"

        if i >= BOX_SLOTS:
            if name in YAWN_BOX_PREP_BANKED_KEYS:
                continue
            if item_id in allowed_modeled:
                continue
            label = name or f"0x{item_id:02x}"
            return f"deep_box_item:{label}@{i}"

        if item_id not in allowed_modeled:
            label = name or f"0x{item_id:02x}"
            return f"disallowed_item_in_box:{label}@{i}"

    return None


def _inventory_name_set(
    inventory_names: list[str] | tuple[str, ...] | None,
) -> set[str]:
    return {canonical_item(str(x)) for x in (inventory_names or []) if str(x).strip()}


def yawn_box_prep_held_reason(
    inventory_names: list[str] | tuple[str, ...] | None,
) -> str | None:
    """Person must hold the shield key and the guns/ammo for this loadout."""
    inv = _inventory_name_set(inventory_names)
    for name in YAWN_BOX_PREP_HELD_KEYS:
        if name not in inv:
            return f"missing_held:{name}"
    for name in YAWN_BOX_PREP_HELD_FIREPOWER:
        if name not in inv:
            return f"missing_held:{name}"
    return None


def yawn_box_prep_capture_ready(
    box: list[tuple[int, int]] | None,
    inventory_names: list[str] | tuple[str, ...] | None,
) -> str | None:
    """Return failure reason when capture preconditions are not met."""
    pollution = yawn_box_prep_box_pollution_reason(box)
    if pollution:
        return pollution
    inv = _inventory_name_set(inventory_names)
    for name in YAWN_BOX_PREP_BANKED_KEYS:
        if not box_has_item(box, name):
            return f"{name}_not_in_box"
        if name in inv:
            return f"{name}_still_held"
    return yawn_box_prep_held_reason(inventory_names)


def yawn_box_prep_ready(state: dict[str, Any] | None) -> bool:
    """True when crest and armor key are banked and firepower is on person."""
    st = state or {}
    return yawn_box_prep_capture_ready(
        box_pairs_from_state(st),
        list(st.get("inventory") or []),
    ) is None


def yawn_box_prep_exit_met(
    state: dict[str, Any] | None,
    prev_state: dict[str, Any] | None,
    progress: Any,
) -> bool:
    """Success: leave 118 into 10B with crest/armor banked and firepower on person."""
    st = state or {}
    if str(st.get("room_id", "")).upper() != YAWN_BOX_PREP_EXIT_ROOM:
        return False
    immediate = (
        prev_state is not None
        and str(prev_state.get("room_id", "")).upper() == YAWN_BOX_PREP_ROOM
    )
    latched = bool(
        progress is not None
        and progress.leg_entered_from(YAWN_BOX_PREP_EXIT_ROOM, {YAWN_BOX_PREP_ROOM})
    )
    if not (immediate or latched):
        return False
    return yawn_box_prep_ready(st)


def _on_yawn_box_prep_leg(planner: Any) -> bool:
    obj = (planner.current_objective() or {}) if planner is not None else {}
    return str(obj.get("checkpoint_id") or "") == YAWN_BOX_PREP_CHECKPOINT_ID


def should_suppress_wrong_room(
    planner: Any,
    prev_room: str,
    room: str,
    state: dict[str, Any] | None,
) -> bool:
    """Skip terminal wrong_room for a successful 118→10B exit after box prep."""
    if not _on_yawn_box_prep_leg(planner):
        return False
    if str(prev_room).upper() != YAWN_BOX_PREP_ROOM:
        return False
    if str(room).upper() != YAWN_BOX_PREP_EXIT_ROOM:
        return False
    return yawn_box_prep_ready(state)


def yawn_box_prep_capture_room_ok(
    completed_cid: str,
    room_id: str,
    expected_room: str,
) -> bool:
    """Allow cp89 capture in the east-stairs exit room (prep room is 118)."""
    if completed_cid != YAWN_BOX_PREP_CHECKPOINT_ID:
        return False
    rid = str(room_id or "").upper()
    if rid == YAWN_BOX_PREP_EXIT_ROOM:
        return True
    exp = str(expected_room or "").upper()
    return bool(exp and rid == exp)
