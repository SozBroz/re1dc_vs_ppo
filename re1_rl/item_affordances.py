"""Key-item affordance obs (north star A2/A3): what held keys are *for*.

Evil Resource item pages describe where to **use** a key and what it **unlocks**,
not a bag of pickup+unlock rooms. Slot layout stays 8×5 = 40 dims (no transplant).

Per-slot floats (see `.cursor/plans/evilresource_key_affordances.plan.md`):
  0  item-id proxy (len(name)/32)
  1  primary USE room index / 128
  2  affordant-here (standing at a use site)
  3  unlock target / path hint (to_room if affordant on a door; else next hop)
  4  currently in inventory
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.item_todo import canonical_item
from re1_rl.room_graph import RoomGraph

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PATH = _ROOT / "data" / "item_affordances.json"
_DOORS_EMPIRICAL = _ROOT / "data" / "doors_empirical.json"
_DOORS_RDT = _ROOT / "data" / "doors_rdt.json"

AFFORDANCE_SLOTS = 8
AFFORDANCE_SLOT_DIM = 5
AFFORDANCES_DIM = AFFORDANCE_SLOTS * AFFORDANCE_SLOT_DIM


@lru_cache(maxsize=1)
def load_affordances(path: str = str(_DEFAULT_PATH)) -> dict[str, dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return {}
    return {k: _normalize_entry(v) for k, v in raw.items() if isinstance(v, dict)}


@lru_cache(maxsize=1)
def _door_graph() -> RoomGraph:
    return RoomGraph(_DOORS_EMPIRICAL, _DOORS_RDT)


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Prefer use_rooms + door_edges; fall back to legacy rooms bag."""
    use_rooms = [str(r) for r in (entry.get("use_rooms") or []) if r]
    edges_raw = entry.get("door_edges") or []
    door_edges: list[dict[str, str]] = []
    for e in edges_raw:
        if not isinstance(e, dict):
            continue
        fr = str(e.get("from_room") or "").strip()
        to = str(e.get("to_room") or "").strip()
        if not fr or not to:
            continue
        door_id = str(e.get("door_id") or f"{fr}->{to}")
        door_edges.append({"from_room": fr, "to_room": to, "door_id": door_id})

    legacy = [str(r) for r in (entry.get("rooms") or []) if r]
    if not use_rooms and not door_edges and legacy:
        # Legacy: treat bag as use sites (old encoder semantics).
        use_rooms = list(legacy)

    use_sites = list(dict.fromkeys(use_rooms + [e["from_room"] for e in door_edges]))
    unlock_rooms = list(dict.fromkeys(e["to_room"] for e in door_edges))
    rooms = list(dict.fromkeys(use_sites + unlock_rooms + legacy))

    return {
        "use_rooms": use_rooms,
        "door_edges": door_edges,
        "pickup_rooms": [str(r) for r in (entry.get("pickup_rooms") or []) if r],
        "rooms": rooms,
        "notes": str(entry.get("notes") or ""),
    }


def _item_sort_key(name: str) -> str:
    return name


def _room_norm(room_id: str, room_index: dict[str, int]) -> float:
    return room_index.get(room_id, 127) / 128.0


def _use_sites(entry: dict[str, Any]) -> list[str]:
    sites = list(entry.get("use_rooms") or [])
    for e in entry.get("door_edges") or []:
        fr = e.get("from_room")
        if fr and fr not in sites:
            sites.append(fr)
    return sites


def _primary_use_room(entry: dict[str, Any]) -> str | None:
    use_rooms = entry.get("use_rooms") or []
    if use_rooms:
        return str(use_rooms[0])
    edges = entry.get("door_edges") or []
    if edges:
        return str(edges[0]["from_room"])
    rooms = entry.get("rooms") or []
    return str(rooms[0]) if rooms else None


def _unlock_target_here(entry: dict[str, Any], room: str) -> str | None:
    """If standing at a keyed door, return its destination; else None (desk/puzzle)."""
    for e in entry.get("door_edges") or []:
        if e.get("from_room") == room:
            return str(e["to_room"])
    return None


def _path_hint_room(
    entry: dict[str, Any],
    current_room: str,
    graph: RoomGraph | None,
) -> str | None:
    """Next hop toward nearest use site, or unlock to_room when already there."""
    sites = _use_sites(entry)
    if not sites:
        return None
    if current_room in sites:
        return _unlock_target_here(entry, current_room)

    if graph is None:
        return sites[0]

    best_site: str | None = None
    best_dist: int | None = None
    for site in sites:
        d = graph.hop_distance(current_room, site)
        if d is None:
            continue
        if best_dist is None or d < best_dist:
            best_dist = d
            best_site = site
    if best_site is None:
        return sites[0]
    hop = graph.next_hop(current_room, best_site)
    return hop or best_site


def encode_affordances(
    *,
    ever_held: set[str] | frozenset[str] | None,
    inventory_slots: list[dict[str, Any]] | None,
    current_room: str | None,
    room_index: dict[str, int],
    affordances_path: str | None = None,
    graph: RoomGraph | None = None,
) -> np.ndarray:
    """Top-K held key items: id, primary USE, affordant-here, unlock/hint, in-inv."""
    v = np.zeros(AFFORDANCES_DIM, dtype=np.float32)
    data = (
        load_affordances(affordances_path)
        if affordances_path is not None
        else load_affordances()
    )
    if not data:
        return v

    held_set = {canonical_item(x) for x in (ever_held or ())}
    inv_names: set[str] = set()
    for slot in inventory_slots or []:
        if isinstance(slot, (list, tuple)) and slot:
            inv_names.add(canonical_item(str(slot[0])))
        elif isinstance(slot, dict):
            inv_names.add(
                canonical_item(str(slot.get("item_id_name") or slot.get("name") or ""))
            )
    inv_names.discard("")

    candidates = sorted(
        (n for n in held_set if n in data),
        key=_item_sort_key,
    )[:AFFORDANCE_SLOTS]

    room = str(current_room or "")
    door_graph = graph if graph is not None else _door_graph()

    for slot_i, name in enumerate(candidates):
        entry = data[name]
        base = slot_i * AFFORDANCE_SLOT_DIM
        v[base] = min(len(name), 32) / 32.0

        primary = _primary_use_room(entry)
        sites = _use_sites(entry)
        if primary:
            v[base + 1] = _room_norm(primary, room_index)
        v[base + 2] = 1.0 if room and room in sites else 0.0

        hint = _path_hint_room(entry, room, door_graph) if room else None
        if hint:
            v[base + 3] = _room_norm(hint, room_index)

        v[base + 4] = 1.0 if name in inv_names else 0.0

    return v
