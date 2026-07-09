"""Enemy slots of the spatial obs + proprio enemy_count, from synthetic
state dicts (the live enemy RAM table is still being hunted; the encoder
contract must hold the moment memory_map.ENEMY_TABLE_BASE is filled in)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.memory_map import decode_enemy_table, enemy_table_fields
from re1_rl.obs_encoder import PROPRIO_FIELDS, ObsEncoder
from re1_rl.room_graph import RoomGraph
from re1_rl.spatial_encoder import ENEMY_SLOTS, SPATIAL_FIELDS, SpatialEncoder

ROOMS = PROJECT_ROOT / "data" / "rooms.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"
IDX = {name: i for i, (name, _) in enumerate(SPATIAL_FIELDS)}
P_IDX = {name: i for i, (name, _) in enumerate(PROPRIO_FIELDS)}


def make_state(enemies, x=10000, z=10000, facing=0):
    return {"room_id": "104", "x": x, "y": 0, "z": z, "facing": facing,
            "hp": 96, "cam_id": 0, "in_control": True, "enemies": enemies}


def zombie(x, z, type_id=1, hp=100, alive=True):
    return {"x": x, "z": z, "type_id": type_id, "hp": hp, "alive": alive}


def test_single_enemy_egocentric():
    enc = SpatialEncoder(None, None)
    # zombie due +z of the player; facing=0 looks along +x -> bearing left
    v = enc.encode(make_state([zombie(10000, 12048)]))
    assert v[IDX["enemy_count"]] == pytest.approx(1 / 10)
    assert abs(v[IDX["enemy0_rel_x"]]) < 1e-6
    assert math.isclose(v[IDX["enemy0_rel_z"]], 2048 / 4096, rel_tol=1e-5)
    assert math.isclose(v[IDX["enemy0_dist"]], 0.5, rel_tol=1e-5)
    assert v[IDX["enemy0_bearing_sin"]] > 0.99  # + = to the left
    assert v[IDX["enemy0_type_id"]] == 1 / 32
    assert math.isclose(v[IDX["enemy0_hp"]], 100 / 255, rel_tol=1e-5)
    assert v[IDX["enemy0_alive"]] == 1.0


def test_enemies_sorted_nearest_first_and_dead_skipped():
    enc = SpatialEncoder(None, None)
    v = enc.encode(make_state([
        zombie(14000, 10000, type_id=2),          # 4000 away
        zombie(11000, 10000, type_id=3),          # 1000 away -> slot 0
        zombie(10100, 10000, type_id=4, hp=0, alive=False),  # corpse: skipped
    ]))
    assert v[IDX["enemy_count"]] == pytest.approx(2 / 10)
    assert v[IDX["enemy0_type_id"]] == 3 / 32
    assert v[IDX["enemy1_type_id"]] == 2 / 32
    assert v[IDX["enemy2_alive"]] == 0.0  # padded


def test_overflow_beyond_slots_capped():
    enc = SpatialEncoder(None, None)
    v = enc.encode(make_state([zombie(10500 + i * 100, 10000) for i in range(8)]))
    assert v[IDX["enemy_count"]] == pytest.approx(8 / 10)
    assert all(v[IDX[f"enemy{i}_alive"]] == 1.0 for i in range(ENEMY_SLOTS))


def test_no_enemies_all_zero():
    enc = SpatialEncoder(None, None)
    v = enc.encode(make_state([]))
    assert v[IDX["enemy_count"]] == 0.0
    assert all(v[IDX[f"enemy{i}_alive"]] == 0.0 for i in range(ENEMY_SLOTS))


def test_proprio_enemy_count_wired():
    enc = ObsEncoder(ROOMS, RoomGraph(DOORS))
    s = make_state([zombie(11000, 10000), zombie(12000, 10000)])
    s.update({"character_id": 1, "inventory": []})
    v = enc.encode_proprio(s, prev_hp=96)
    assert v[P_IDX["enemy_count"]] == pytest.approx(2 / 10)
    assert v[P_IDX["interaction_prompt"]] == 0.0


def test_enemy_table_fields_mapped() -> None:
    """HP at slot base -> six RAM fields and decode with slot index."""
    fields = enemy_table_fields()
    assert len(fields) == 6
    ram = {f"enemy{i}_hp": 100 if i == 1 else 0 for i in range(6)}
    decoded = decode_enemy_table(ram)
    assert len(decoded) == 1
    assert decoded[0]["slot"] == 1
    assert decoded[0]["hp"] == 100


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
