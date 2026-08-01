"""Enemy active_byte / hittable spatial fields."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.spatial_encoder import ENEMY_SLOT_DIM, ENEMY_SLOTS, ITEM_SLOTS, SPATIAL_DIM, SpatialEncoder


def test_spatial_dim_includes_active_hittable() -> None:
    assert ENEMY_SLOT_DIM == 12
    assert SPATIAL_DIM == 160


def test_encode_active_and_hittable() -> None:
    enc = SpatialEncoder()
    state = {
        "x": 0,
        "z": 0,
        "facing": 0,
        "room_id": "100",
        "enemies": [
            {
                "x": 100,
                "z": 0,
                "type_id": 1,
                "hp": 80,
                "alive": True,
                "active_byte": 0x1C,
                "combat_near": 1,
                "world_vx": 0,
                "world_vz": 0,
            }
        ],
    }
    v = enc.encode(state)
    base = 1 + ITEM_SLOTS * 8 + 1
    assert v[base + 10] == np.float32(0x1C / 255.0)
    assert v[base + 11] == 1.0


def test_hittable_false_when_not_combat_near() -> None:
    enc = SpatialEncoder()
    state = {
        "x": 0,
        "z": 0,
        "facing": 0,
        "room_id": "100",
        "enemies": [
            {
                "x": 100,
                "z": 0,
                "type_id": 1,
                "hp": 80,
                "alive": True,
                "active_byte": 2,
                "combat_near": 0,
            }
        ],
    }
    v = enc.encode(state)
    base = 1 + ITEM_SLOTS * 8 + 1
    assert v[base + 11] == 0.0
