"""Tests for dynamic world_state observation encoder."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from re1_rl.item_todo import RoomItems
from re1_rl.world_catalog import WorldCatalog
from re1_rl.world_state_encoder import (
    PICKUP_ACTIVE_SLICE,
    WORLD_STATE_DIM,
    encode_world_state,
)

_ROOT = Path(__file__).resolve().parents[1]


def _catalog() -> WorldCatalog:
    return WorldCatalog.from_files(_ROOT)


def _room_items() -> RoomItems:
    return RoomItems(_ROOT / "data" / "room_items.json")


def test_encode_world_state_shape_471() -> None:
    cat = _catalog()
    ri = _room_items()
    v = encode_world_state(
        catalog=cat,
        room_items=ri,
        ever_held=set(),
        inventory_names=set(),
        current_room="105",
    )
    assert v.shape == (WORLD_STATE_DIM,)
    assert WORLD_STATE_DIM == 471
    assert v.dtype == np.float32


def test_pickup_active_prunes_emblem() -> None:
    cat = _catalog()
    ri = _room_items()
    before = encode_world_state(
        catalog=cat,
        room_items=ri,
        ever_held=set(),
        inventory_names=set(),
        current_room="105",
    )
    after = encode_world_state(
        catalog=cat,
        room_items=ri,
        ever_held={"emblem"},
        inventory_names=set(),
        current_room="105",
    )
    assert after[PICKUP_ACTIVE_SLICE].sum() < before[PICKUP_ACTIVE_SLICE].sum()

    emblem_rows = [
        i
        for i, item in enumerate(cat._iter_pickup_rows())
        if item.get("name") == "emblem"
    ]
    assert emblem_rows
    emblem_i = emblem_rows[0]
    assert before[PICKUP_ACTIVE_SLICE][emblem_i] == 1.0
    assert after[PICKUP_ACTIVE_SLICE][emblem_i] == 0.0
