"""Tests for static WorldCatalog almanac buffers."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from re1_rl.key_items import KEY_ITEM_NAMES
from re1_rl.world_catalog import MAX_NEIGHBORS, NUM_ROOMS, PAD_ROOM, WorldCatalog

_ROOT = Path(__file__).resolve().parents[1]


def _catalog() -> WorldCatalog:
    return WorldCatalog.from_files(_ROOT)


def test_buffer_shapes_and_dtypes() -> None:
    cat = _catalog()
    assert cat.map_neighbors.shape == (NUM_ROOMS, MAX_NEIGHBORS)
    assert cat.map_degree.shape == (NUM_ROOMS,)
    assert cat.room_area.shape == (NUM_ROOMS,)
    assert cat.room_stage.shape == (NUM_ROOMS,)
    assert cat.link_requires_key.shape == (NUM_ROOMS, MAX_NEIGHBORS)

    assert cat.num_pickups == 121
    assert cat.pickup_room_idx.shape == (121,)
    assert cat.pickup_item_id.shape == (121,)
    assert cat.pickup_category.shape == (121,)
    assert cat.pickup_key_flag.shape == (121,)
    assert cat.pickup_gate_type.shape == (121,)
    assert cat.pickup_requires_mask.shape == (121, len(KEY_ITEM_NAMES))

    k = len(KEY_ITEM_NAMES)
    assert cat.key_pickup_room.shape == (k,)
    assert cat.key_use_room.shape == (k,)
    assert cat.key_unlock_room.shape == (k,)
    assert cat.key_door_from.shape == (k,)
    assert cat.key_item_id.shape == (k,)

    assert cat.num_files >= 1
    assert cat.file_room_idx.shape == (cat.num_files,)
    assert cat.file_code_const.shape[0] == cat.num_files

    assert cat.num_combine >= 4
    assert cat.combine_src_a.shape == (cat.num_combine,)
    assert cat.combine_dst.max() <= 0x4B

    for arr in (
        cat.map_neighbors,
        cat.pickup_item_id,
        cat.key_item_id,
        cat.combine_dst,
    ):
        assert arr.dtype == np.float32


def test_map_degree_at_most_six() -> None:
    cat = _catalog()
    assert float(cat.map_degree.max()) <= MAX_NEIGHBORS
    assert np.all(cat.map_degree >= 0)


def test_room_105_neighbors_include_tea_or_main_hall() -> None:
    cat = _catalog()
    idx_105 = cat.room_index["105"]
    idx_104 = cat.room_index["104"]
    idx_106 = cat.room_index["106"]
    nbrs = {int(x) for x in cat.map_neighbors[idx_105] if int(x) != PAD_ROOM}
    assert idx_104 in nbrs or idx_106 in nbrs


def test_pickup_active_mask_prunes_held_and_gated() -> None:
    cat = _catalog()
    all_active = cat.pickup_active_mask(set())
    assert all_active.shape == (121,)
    assert all_active.sum() > 0

    held = {"emblem"}
    after_emblem = cat.pickup_active_mask(held)
    assert after_emblem.sum() < all_active.sum()

    # shield_key gated on gold_emblem — not active until requirement held.
    shield_idx = KEY_ITEM_NAMES.index("shield_key")
    shield_gated = int(np.where(cat.pickup_item_id == cat.key_item_id[shield_idx])[0][0])
    assert after_emblem[shield_gated] == 0.0

    with_gold = cat.pickup_active_mask({"emblem", "gold_emblem", "music_notes"})
    assert with_gold[shield_gated] == 1.0


def test_emblem_key_pickup_room() -> None:
    cat = _catalog()
    emblem_i = KEY_ITEM_NAMES.index("emblem")
    dining_idx = float(cat.room_index["105"])
    assert cat.key_pickup_room[emblem_i] == dining_idx


def test_torch_buffers_roundtrip() -> None:
    torch = __import__("torch")
    cat = _catalog()
    buffers = cat.as_torch_buffers()
    assert set(buffers) >= {"map_neighbors", "pickup_requires_mask", "combine_dst"}
    assert buffers["map_neighbors"].dtype == torch.float32
    assert buffers["map_neighbors"].shape == (NUM_ROOMS, MAX_NEIGHBORS)
