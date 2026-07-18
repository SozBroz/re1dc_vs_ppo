"""Dynamic mansion memory for world-aware policy (rollout-side only).

Packed into a single ``world_state`` vector — key hints are folded in (no separate
``key_hints`` obs key). Static pickup/key topology lives in :class:`WorldCatalog`.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from re1_rl.item_affordances import KEY_HINTS_DIM, encode_key_hints
from re1_rl.item_todo import RoomItems, canonical_item
from re1_rl.key_items import KEY_ITEM_NAMES
from re1_rl.world_catalog import NUM_ROOMS, WorldCatalog

NUM_PICKUPS = 121
KEY_HINTS_PER_KEY = 3

PICKUP_ACTIVE_SLICE = slice(0, NUM_PICKUPS)
PICKUP_GATED_SLICE = slice(NUM_PICKUPS, 2 * NUM_PICKUPS)
ROOM_REMAINING_SLICE = slice(2 * NUM_PICKUPS, 2 * NUM_PICKUPS + NUM_ROOMS)
KEY_PICKUP_PENDING_SLICE = slice(370, 405)
KEY_USE_PENDING_SLICE = slice(405, 440)
KEY_AFFORDANT_HERE_SLICE = slice(440, 475)

WORLD_STATE_DIM = 475


def _pickup_gated_mask(
    catalog: WorldCatalog,
    ever_held: set[str] | frozenset[str],
) -> np.ndarray:
    """1.0 for catalog pickups that exist, are not held, and are not obtainable."""
    held = {canonical_item(n) for n in ever_held}
    mask = np.zeros(catalog.num_pickups, dtype=np.float32)
    for i, item in enumerate(catalog._iter_pickup_rows()):
        name = str(item.get("name", ""))
        if name in held:
            continue
        if not RoomItems._obtainable(item, held):
            mask[i] = 1.0
    return mask


def _encode_room_remaining(
    catalog: WorldCatalog,
    room_items: RoomItems,
    ever_held: set[str] | frozenset[str],
) -> np.ndarray:
    held = {canonical_item(n) for n in ever_held}
    out = np.zeros(NUM_ROOMS, dtype=np.float32)
    inv = {idx: rid for rid, idx in catalog.room_index.items()}
    for idx in range(NUM_ROOMS):
        room_id = inv.get(idx)
        if room_id is None:
            continue
        out[idx] = room_items.remaining_in_room(room_id, held) / 4.0
    return out


def _pack_key_hints(key_hints_vec: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(KEY_ITEM_NAMES)
    pickup = np.zeros(n, dtype=np.float32)
    use = np.zeros(n, dtype=np.float32)
    affordant = np.zeros(n, dtype=np.float32)
    for i in range(n):
        base = i * KEY_HINTS_PER_KEY
        pickup[i] = key_hints_vec[base]
        use[i] = key_hints_vec[base + 1]
        affordant[i] = key_hints_vec[base + 2]
    return pickup, use, affordant


def encode_world_state(
    *,
    catalog: WorldCatalog,
    room_items: RoomItems,
    ever_held: set[str] | frozenset[str] | None,
    inventory_names: set[str] | frozenset[str] | list[str] | None,
    current_room: str | None,
    key_hints_vec: np.ndarray | None = None,
) -> np.ndarray:
    """Encode dynamic mansion masks for the world-aware extractor."""
    v = np.zeros(WORLD_STATE_DIM, dtype=np.float32)
    held = ever_held or set()

    v[PICKUP_ACTIVE_SLICE] = catalog.pickup_active_mask(held)
    v[PICKUP_GATED_SLICE] = _pickup_gated_mask(catalog, held)
    v[ROOM_REMAINING_SLICE] = _encode_room_remaining(catalog, room_items, held)

    if key_hints_vec is None:
        key_hints_vec = encode_key_hints(
            ever_held=held,
            inventory_names=inventory_names,
            current_room=current_room,
        )
    else:
        key_hints_vec = np.asarray(key_hints_vec, dtype=np.float32)
        if key_hints_vec.shape != (KEY_HINTS_DIM,):
            raise ValueError(
                f"key_hints_vec must be ({KEY_HINTS_DIM},), got {key_hints_vec.shape}"
            )

    pickup_pending, use_pending, affordant_here = _pack_key_hints(key_hints_vec)
    v[KEY_PICKUP_PENDING_SLICE] = pickup_pending
    v[KEY_USE_PENDING_SLICE] = use_pending
    v[KEY_AFFORDANT_HERE_SLICE] = affordant_here
    return v
