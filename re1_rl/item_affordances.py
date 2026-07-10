"""Key-item affordance obs (north star A2/A3): what held keys are for."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.item_todo import canonical_item

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "item_affordances.json"

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
    return raw if isinstance(raw, dict) else {}


def _item_sort_key(name: str) -> str:
    return name


def encode_affordances(
    *,
    ever_held: set[str] | frozenset[str] | None,
    inventory_slots: list[dict[str, Any]] | None,
    current_room: str | None,
    room_index: dict[str, int],
) -> np.ndarray:
    """Top-K held key items: id, primary room, affordant-here, room count, in-inventory."""
    v = np.zeros(AFFORDANCES_DIM, dtype=np.float32)
    data = load_affordances()
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
    for slot_i, name in enumerate(candidates):
        entry = data[name]
        rooms = [str(r) for r in entry.get("rooms", []) if r]
        base = slot_i * AFFORDANCE_SLOT_DIM
        v[base] = min(len(name), 32) / 32.0
        if rooms:
            primary = rooms[0]
            v[base + 1] = room_index.get(primary, 127) / 128.0
            v[base + 2] = 1.0 if room in rooms else 0.0
            v[base + 3] = min(len(rooms), 16) / 16.0
        v[base + 4] = 1.0 if name in inv_names else 0.0

    return v
