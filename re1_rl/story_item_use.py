"""Position-gated inventory USE for story / puzzle key items.

Legal mask / affordance is **key item in inventory + room + standing position
only** — never facing, interaction prompt, or anim state.  Failed USE (wrong
facing, menu rejected, etc.) does not consume the site; the mask stays legal
until a rewarded success.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import ITEM_IDS, SCENE_FLAG

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SITES_PATH = _ROOT / "data" / "story_item_use_sites.json"

# Wooden emblem USE site; same stand as gold_emblem pickup (data/rdt_item_positions).
ALCOVE_EMBLEM_SITE_ID = "emblem@10F_alcove"

_NAME_TO_ITEM_ID = {name: iid for iid, name in ITEM_IDS.items()}


@lru_cache(maxsize=1)
def load_story_use_sites(path: str = str(_DEFAULT_SITES_PATH)) -> tuple[dict[str, Any], ...]:
    p = Path(path)
    if not p.is_file():
        return ()
    with p.open(encoding="utf-8") as f:
        raw = json.load(f)
    rows = raw.get("sites") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return ()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = canonical_item(str(row.get("item", "")))
        room = str(row.get("room", "")).strip()
        site_id = str(row.get("id", "")).strip() or f"{item}@{room}"
        if not item or not room:
            continue
        out.append(
            {
                "id": site_id,
                "item": item,
                "room": room,
                "x": float(row.get("x", 0)),
                "z": float(row.get("z", 0)),
                "radius": float(row.get("radius", 1500)),
                "consumes": bool(row.get("consumes", False)),
                "notes": str(row.get("notes", "")),
            }
        )
    return tuple(out)


def _dist(px: float, pz: float, x: float, z: float) -> float:
    return math.hypot(px - x, pz - z)


def _slot_item_id(inventory: list[tuple[int, int]], slot: int) -> int:
    if slot < 0 or slot >= len(inventory):
        return 0
    return int(inventory[slot][0]) & 0xFF


def _slot_item_name(inventory: list[tuple[int, int]], slot: int) -> str:
    item_id = _slot_item_id(inventory, slot)
    return ITEM_IDS.get(item_id, "")


def _slot_qty(inventory: list[tuple[int, int]], slot: int) -> int:
    if slot < 0 or slot >= len(inventory):
        return 0
    return int(inventory[slot][1])


def _story_item_names() -> frozenset[str]:
    return frozenset(str(s["item"]) for s in load_story_use_sites())


def _slot_holds_story_key(
    inventory: list[tuple[int, int]],
    slot: int,
    *,
    item_name: str | None = None,
) -> bool:
    """RE1 PS1 often stores unique key items as (id, qty=0) — same as knife."""
    iid = _slot_item_id(inventory, slot)
    if not iid:
        return False
    name = canonical_item(_slot_item_name(inventory, slot))
    if not name:
        return False
    if item_name is not None and canonical_item(item_name) != name:
        return False
    qty = _slot_qty(inventory, slot)
    if qty > 0:
        return True
    return name in _story_item_names()


def _held_key_item_ids(inventory: list[tuple[int, int]]) -> set[int]:
    held: set[int] = set()
    for i in range(len(inventory)):
        item_id = _slot_item_id(inventory, i)
        if item_id and _slot_holds_story_key(inventory, i):
            held.add(item_id)
    return held


def legal_story_use_slots(
    inventory: list[tuple[int, int]] | None,
    *,
    room: str | None,
    x: float | int | None,
    z: float | int | None,
    rewarded_site_ids: set[str] | frozenset[str] | None = None,
    sites_path: str | None = None,
) -> list[int]:
    """Inventory slots whose key item may be USEd at the current stand position."""
    if inventory is None:
        return []
    sites = matching_story_sites(
        room=room,
        x=x,
        z=z,
        inventory=inventory,
        rewarded_site_ids=rewarded_site_ids,
        sites_path=sites_path,
    )
    if not sites:
        return []
    site_items = {str(s["item"]) for s in sites}
    slots: list[int] = []
    for i in range(len(inventory)):
        if not _slot_holds_story_key(inventory, i):
            continue
        name = canonical_item(_slot_item_name(inventory, i))
        if name and name in site_items:
            slots.append(i)
    return slots


def matching_story_sites(
    *,
    room: str | None,
    x: float | int | None,
    z: float | int | None,
    inventory: list[tuple[int, int]] | None,
    rewarded_site_ids: set[str] | frozenset[str] | None = None,
    sites_path: str | None = None,
) -> list[dict[str, Any]]:
    """Sites affordant at the current stand position with the key item in inventory."""
    if not room or inventory is None:
        return []
    px = float(x or 0)
    pz = float(z or 0)
    rewarded = set(rewarded_site_ids or ())
    held_ids = _held_key_item_ids(inventory)
    sites = (
        load_story_use_sites(sites_path)
        if sites_path is not None
        else load_story_use_sites()
    )
    hits: list[dict[str, Any]] = []
    for site in sites:
        if str(site["id"]) in rewarded:
            continue
        if str(site["room"]) != str(room):
            continue
        item_id = _NAME_TO_ITEM_ID.get(str(site["item"]), 0)
        if item_id not in held_ids:
            continue
        if _dist(px, pz, float(site["x"]), float(site["z"])) > float(site["radius"]):
            continue
        hits.append(site)
    return hits


def story_site_for_slot(
    inventory: list[tuple[int, int]],
    slot: int,
    *,
    room: str | None,
    x: float | int | None,
    z: float | int | None,
    rewarded_site_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any] | None:
    name = _slot_item_name(inventory, slot)
    if not name:
        return None
    for site in matching_story_sites(
        room=room,
        x=x,
        z=z,
        inventory=inventory,
        rewarded_site_ids=rewarded_site_ids,
    ):
        if str(site["item"]) == canonical_item(name):
            return site
    return None


def slot_legal_for_story_use(
    inventory: list[tuple[int, int]] | None,
    slot: int,
    *,
    room: str | None,
    x: float | int | None,
    z: float | int | None,
    rewarded_site_ids: set[str] | frozenset[str] | None = None,
) -> bool:
    if inventory is None:
        return False
    return int(slot) in legal_story_use_slots(
        inventory,
        room=room,
        x=x,
        z=z,
        rewarded_site_ids=rewarded_site_ids,
    )


def any_legal_story_use_slot(
    inventory: list[tuple[int, int]] | None,
    *,
    room: str | None,
    x: float | int | None,
    z: float | int | None,
    rewarded_site_ids: set[str] | frozenset[str] | None = None,
) -> bool:
    return bool(
        legal_story_use_slots(
            inventory,
            room=room,
            x=x,
            z=z,
            rewarded_site_ids=rewarded_site_ids,
        )
    )


def story_use_succeeded(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    site: dict[str, Any],
    slot: int,
    inventory_before: list[tuple[int, int]],
    inventory_after: list[tuple[int, int]],
) -> bool:
    """Heuristic success when the game accepted a story USE."""
    return story_use_macro_resolved(
        before=before,
        after=after,
        site=site,
        slot=slot,
        inventory_before=inventory_before,
        inventory_after=inventory_after,
        allow_menu_msg_only=True,
    )


def story_use_macro_resolved(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    site: dict[str, Any],
    slot: int,
    inventory_before: list[tuple[int, int]],
    inventory_after: list[tuple[int, int]],
    allow_menu_msg_only: bool = False,
) -> bool:
    """Story USE resolved — ignores pause-menu msg bit unless allow_menu_msg_only."""
    item_id = _NAME_TO_ITEM_ID.get(str(site["item"]), 0)
    qty_before = int(inventory_before[slot][1]) if slot < len(inventory_before) else 0
    qty_after = int(inventory_after[slot][1]) if slot < len(inventory_after) else 0
    id_after = _slot_item_id(inventory_after, slot)
    consumed = bool(site.get("consumes")) and (
        qty_after < qty_before or id_after == 0 or id_after != item_id
    )
    if consumed:
        return True

    in_menu = bool(after.get("in_item_menu", False))
    scene_before = int(before.get("scene_flag", 0))
    scene_after = int(after.get("scene_flag", 0))
    scene_changed = scene_before != scene_after
    in_control_before = bool(before.get("in_control", True))
    in_control_after = bool(after.get("in_control", True))
    cutscene_started = in_control_before and not in_control_after
    msg_before = int(before.get("msg_flag", 0))
    msg_after = int(after.get("msg_flag", 0))
    msg_changed = msg_before != msg_after

    if not in_menu:
        if scene_changed or bool(after.get("scene_active", False)):
            return True
        if cutscene_started:
            return True

    if allow_menu_msg_only and msg_changed:
        return True
    return False


def _inventory_holds_named_key(
    inventory: list[tuple[int, int]],
    item_name: str,
) -> bool:
    item_id = _NAME_TO_ITEM_ID.get(canonical_item(item_name), 0)
    if not item_id:
        return False
    for i in range(len(inventory)):
        if _slot_item_id(inventory, i) != item_id:
            continue
        if _slot_holds_story_key(inventory, i, item_name=item_name):
            return True
    return False


def _alcove_site(sites_path: str | None = None) -> dict[str, Any] | None:
    sites = (
        load_story_use_sites(sites_path)
        if sites_path is not None
        else load_story_use_sites()
    )
    for site in sites:
        if str(site["id"]) == ALCOVE_EMBLEM_SITE_ID:
            return site
    return None


def gold_emblem_return_detected(
    *,
    prev_state: dict[str, Any],
    inventory_before: list[tuple[int, int]] | None,
    inventory_after: list[tuple[int, int]] | None,
    sites_path: str | None = None,
) -> bool:
    """True when gold_emblem left inventory at the 10F alcove (put-back, not swap).

    Intended swap keeps gold and USEs wooden ``emblem`` — that path must not
    trip this. Detection is RAM inventory delta + stand position only.
    """
    if not inventory_before or not inventory_after:
        return False
    if str(prev_state.get("room_id", "")) != "10F":
        return False
    site = _alcove_site(sites_path)
    if site is None:
        return False
    px = float(prev_state.get("x") or 0)
    pz = float(prev_state.get("z") or 0)
    if _dist(px, pz, float(site["x"]), float(site["z"])) > float(site["radius"]):
        return False
    if not _inventory_holds_named_key(inventory_before, "gold_emblem"):
        return False
    if _inventory_holds_named_key(inventory_after, "gold_emblem"):
        return False
    return True


def annotate_story_use_success(
    state: dict[str, Any],
    *,
    prev_state: dict[str, Any],
    inventory_before: list[tuple[int, int]] | None,
    inventory_after: list[tuple[int, int]] | None,
    rewarded_site_ids: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Set ``story_use_success`` / ``gold_emblem_return`` from inventory USE deltas."""
    if state.get("story_use_success") or state.get("gold_emblem_return"):
        return state
    if not inventory_before or not inventory_after:
        return state

    # Put-back first: gold left at alcove ⇒ never pay wooden emblem story_use.
    if gold_emblem_return_detected(
        prev_state=prev_state,
        inventory_before=inventory_before,
        inventory_after=inventory_after,
    ):
        out = dict(state)
        out["gold_emblem_return"] = True
        return out

    sites = matching_story_sites(
        room=str(prev_state.get("room_id", "")),
        x=prev_state.get("x"),
        z=prev_state.get("z"),
        inventory=inventory_before,
        rewarded_site_ids=rewarded_site_ids,
    )
    for site in sites:
        item_id = _NAME_TO_ITEM_ID.get(str(site["item"]), 0)
        slot: int | None = None
        for i in range(min(8, len(inventory_before))):
            if _slot_item_id(inventory_before, i) != item_id:
                continue
            if not _slot_holds_story_key(
                inventory_before, i, item_name=str(site["item"])
            ):
                continue
            slot = i
            break
        if slot is None:
            continue
        before_probe = {
            "scene_flag": int(prev_state.get("scene_flag", 0) or 0),
            "msg_flag": int(prev_state.get("msg_flag", 0) or 0),
            "in_control": bool(prev_state.get("in_control", True)),
            "in_item_menu": False,
            "scene_active": bool(prev_state.get("scene_active", False)),
        }
        after_probe = {
            "scene_flag": int(state.get("scene_flag", 0) or 0),
            "msg_flag": int(state.get("msg_flag", 0) or 0),
            "in_control": bool(state.get("in_control", True)),
            "in_item_menu": False,
            "scene_active": bool(state.get("scene_active", False)),
        }
        if story_use_macro_resolved(
            before=before_probe,
            after=after_probe,
            site=site,
            slot=slot,
            inventory_before=inventory_before,
            inventory_after=inventory_after,
        ):
            out = dict(state)
            out["story_use_success"] = str(site["id"])
            return out
    return state


def read_story_use_probe(client: Any) -> dict[str, Any]:
    from re1_rl.memory_map import GAME_MODE, GAME_STATE, IN_CONTROL_MASK, MESSAGE_FLAG
    from re1_rl.ram_skip import item_inventory_screen_from_ram, scene_active_from_ram

    ram = client.read_ram(
        [
            ("scene_flag", SCENE_FLAG, "u8"),
            ("msg_flag", MESSAGE_FLAG, "u8"),
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
        ]
    )
    in_item_menu = item_inventory_screen_from_ram(ram)
    return {
        "scene_flag": int(ram.get("scene_flag", 0)),
        "msg_flag": int(ram.get("msg_flag", 0)),
        "game_mode": int(ram.get("game_mode", 0)),
        "in_control": bool(int(ram.get("game_mode", 0)) & IN_CONTROL_MASK),
        "game_state": int(ram.get("game_state", 0)),
        "in_item_menu": in_item_menu,
        "scene_active": scene_active_from_ram(ram),
    }
