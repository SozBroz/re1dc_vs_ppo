"""Tests for RDT merge + static enemy spatial fallback."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from re1_rl.spatial_encoder import SPATIAL_FIELDS, SpatialEncoder, StaticEnemySpawns


def test_static_enemy_spawns_encode(tmp_path: Path):
    path = tmp_path / "room_enemies.json"
    path.write_text(json.dumps({
        "104": {
            "room_name": "TEA ROOM",
            "enemies": [{"enemy_type": "zombie", "x": 4150, "z": 6300, "model_id": 17}],
        }
    }), encoding="utf-8")
    static = StaticEnemySpawns(path)
    enc = SpatialEncoder(None, None, static)
    state = {"room_id": "104", "x": 4000, "z": 6000, "facing": 0, "enemies": []}
    v = enc.encode(state)
    idx = next(i for i, (n, _) in enumerate(SPATIAL_FIELDS) if n == "enemy_count")
    assert v[idx] > 0


def test_static_enemies_skipped_when_live_present(tmp_path: Path):
    path = tmp_path / "room_enemies.json"
    path.write_text(json.dumps({
        "104": {"enemies": [{"x": 1, "z": 2, "model_id": 1}]},
    }), encoding="utf-8")
    enc = SpatialEncoder(None, None, StaticEnemySpawns(path))
    state = {
        "room_id": "104", "x": 0, "z": 0, "facing": 0,
        "enemies": [{"x": 9000, "z": 9000, "type_id": 1, "hp": 100, "alive": True}],
    }
    v = enc.encode(state)
    rel_x_idx = next(i for i, (n, _) in enumerate(SPATIAL_FIELDS) if n == "enemy0_rel_x")
    assert v[rel_x_idx] != 0  # live enemy at 9000, not static at 1
