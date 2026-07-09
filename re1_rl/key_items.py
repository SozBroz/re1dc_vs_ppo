"""Ever-held key-item bitmask for privileged obs (north star A7)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from re1_rl.item_todo import canonical_item, canonicalize

_DEFAULT_ROOM_ITEMS = (
    Path(__file__).resolve().parents[1] / "data" / "room_items.json"
)


@lru_cache(maxsize=1)
def key_item_names(room_items_path: str = str(_DEFAULT_ROOM_ITEMS)) -> tuple[str, ...]:
    """Stable sorted canonical names for all key_item rows in room_items.json."""
    p = Path(room_items_path)
    if not p.is_file():
        return ()
    with p.open(encoding="utf-8") as f:
        raw = json.load(f)
    names: set[str] = set()
    for block in raw.values():
        if not isinstance(block, dict):
            continue
        for row in block.get("items", []):
            if row.get("key_item"):
                names.add(canonical_item(str(row.get("name", ""))))
    return tuple(sorted(n for n in names if n))


KEY_ITEM_NAMES: tuple[str, ...] = key_item_names()
KEYS_HELD_DIM = len(KEY_ITEM_NAMES)


def encode_keys_held(ever_held: set[str] | frozenset[str] | None) -> np.ndarray:
    """One float per key item: 1.0 if ever obtained this episode, else 0."""
    v = np.zeros(KEYS_HELD_DIM, dtype=np.float32)
    held = canonicalize(ever_held or ())
    for i, name in enumerate(KEY_ITEM_NAMES):
        if name in held:
            v[i] = 1.0
    return v
