"""Offline tests for the egocentric spatial obs (items/exits/visited).

No emulator: synthetic state dicts + tmp_path JSON tables. Enemy slots are
covered separately in test_enemy_encoder.py.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.item_todo import ItemTracker, RoomItems
from re1_rl.room_graph import RoomGraph
from re1_rl.spatial_encoder import (
    MAX_ITEM_ID,
    SPATIAL_DIM,
    SPATIAL_FIELDS,
    VISITED_GRID,
    ItemPositions,
    SpatialEncoder,
    VisitedMask,
)

DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"
IDX = {name: i for i, (name, _) in enumerate(SPATIAL_FIELDS)}

EMBLEM_X, EMBLEM_Z = 30700, 7200  # dining room 105 table anchor


def make_state(room="105", x=28000, z=7200, facing=0, **kw):
    s = {"room_id": room, "x": x, "y": 0, "z": z, "facing": facing,
         "hp": 96, "cam_id": 0, "in_control": True, "enemies": []}
    s.update(kw)
    return s


def make_positions(tmp_path: Path) -> ItemPositions:
    data = {
        "_meta": {"source": "test"},
        "105:emblem": {"x": EMBLEM_X, "z": EMBLEM_Z, "source": "empirical",
                       "confidence": "high", "notes": ""},
    }
    p = tmp_path / "item_positions.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return ItemPositions(p)


def make_room_items(tmp_path: Path) -> RoomItems:
    data = {
        "105": {"room_name": "DINING ROOM", "items": [
            {"name": "emblem", "item_id": 0x1F, "count": 1, "key_item": True,
             "in_inventory_table": True, "notes": ""},
            {"name": "shield_key", "item_id": 0x35, "count": 1, "key_item": True,
             "in_inventory_table": True, "notes": "",
             "gate": {"type": "item", "requires": ["gold_emblem"], "notes": ""}},
        ]},
    }
    p = tmp_path / "room_items.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return RoomItems(p)


def test_spatial_dims_consistent():
    assert len(SPATIAL_FIELDS) == SPATIAL_DIM == 128
    assert len({n for n, _ in SPATIAL_FIELDS}) == SPATIAL_DIM  # unique names


def test_item_bearing_and_distance_dining_emblem(tmp_path):
    """Standing west of the emblem facing +x: item dead ahead, dist correct."""
    enc = SpatialEncoder(make_positions(tmp_path), None)
    room_items = make_room_items(tmp_path)
    tracker = ItemTracker(todo=[])
    s = make_state(x=EMBLEM_X - 2000, z=EMBLEM_Z, facing=0)

    v = enc.encode(s, room_items=room_items, item_tracker=tracker)
    assert v[IDX["item0_rel_x"]] > 0  # emblem to +x
    assert abs(v[IDX["item0_rel_z"]]) < 1e-6
    assert math.isclose(v[IDX["item0_dist"]], 2000 / 4096, rel_tol=1e-5)
    # facing=0 -> theta=0; dz=0, dx>0 -> bearing 0 = dead ahead
    assert abs(v[IDX["item0_bearing_sin"]]) < 1e-6
    assert v[IDX["item0_bearing_cos"]] > 0.99
    assert math.isclose(v[IDX["item0_item_id"]], 0x1F / float(MAX_ITEM_ID), rel_tol=1e-5)
    assert v[IDX["item0_key_item"]] == 1.0
    assert v[IDX["item0_gated"]] == 0.0


def test_item_bearing_sign_behind(tmp_path):
    """Item due -x while facing +x -> bearing_cos negative (behind)."""
    enc = SpatialEncoder(make_positions(tmp_path), None)
    s = make_state(x=EMBLEM_X + 2000, z=EMBLEM_Z, facing=0)
    v = enc.encode(s, room_items=make_room_items(tmp_path),
                   item_tracker=ItemTracker(todo=[]))
    assert v[IDX["item0_bearing_cos"]] < -0.99


def test_gated_item_visible_with_tracked_requirements(tmp_path):
    """shield_key (requires gold_emblem) shows with gated=1, not counted
    in items_obtainable_here; after holding gold_emblem it ungates."""
    enc = SpatialEncoder(make_positions(tmp_path), None)
    room_items = make_room_items(tmp_path)
    tracker = ItemTracker(todo=[])
    s = make_state()

    v = enc.encode(s, room_items=room_items, item_tracker=tracker)
    assert v[IDX["items_obtainable_here"]] == 1 / 8  # emblem only
    gated_flags = [v[IDX[f"item{i}_gated"]] for i in range(2)]
    assert sorted(gated_flags) == [0.0, 1.0]

    tracker.update([("gold_emblem", 1)])
    v2 = enc.encode(s, room_items=room_items, item_tracker=tracker)
    assert v2[IDX["items_obtainable_here"]] == 2 / 8
    assert all(v2[IDX[f"item{i}_gated"]] == 0.0 for i in range(2))


def test_ever_held_item_removed(tmp_path):
    enc = SpatialEncoder(make_positions(tmp_path), None)
    room_items = make_room_items(tmp_path)
    tracker = ItemTracker(todo=[])
    tracker.update([("emblem", 1)])
    v = enc.encode(make_state(), room_items=room_items, item_tracker=tracker)
    assert v[IDX["items_obtainable_here"]] == 0.0
    assert v[IDX["item0_dist"]] == 0.0  # emblem slot gone


def test_unknown_position_item_still_listed(tmp_path):
    """Items without coords expose id/key bits with zero geometry."""
    enc = SpatialEncoder(ItemPositions(tmp_path / "missing.json"), None)
    v = enc.encode(make_state(), room_items=make_room_items(tmp_path),
                   item_tracker=ItemTracker(todo=[]))
    assert v[IDX["item0_dist"]] == 0.0
    assert v[IDX["item0_item_id"]] > 0.0


def test_exits_from_door_graph():
    g = RoomGraph(DOORS)
    enc = SpatialEncoder(None, g)
    door = g.exit_toward("105", "106")
    s = make_state(x=door.x - 1500, z=door.z, facing=0)
    v = enc.encode(s)
    assert v[IDX["num_known_exits"]] > 0.0
    assert abs(v[IDX["exit0_bearing_sin"]]) < 1e-6
    assert v[IDX["exit0_bearing_cos"]] > 0.99
    assert math.isclose(v[IDX["exit0_dist"]], 1500 / 4096, rel_tol=1e-5)


def test_visited_mask_marks_and_resets():
    vm = VisitedMask()
    assert vm.update("105", 1000, 1000) is True
    assert vm.update("105", 1000, 1000) is False  # same cell
    assert vm.update("105", 1000 + 512, 1000) is True  # 2 cells over
    plane = vm.plane("105")
    assert plane.shape == (VISITED_GRID, VISITED_GRID, 1)
    assert plane.sum() == 2.0
    assert vm.plane("999").sum() == 0.0  # unseen room -> zeros
    vm.reset()
    assert vm.plane("105").sum() == 0.0


def test_visited_mask_clips_at_grid_edge():
    vm = VisitedMask()
    vm.update("105", 0, 0)
    vm.update("105", 10_000_000, -10_000_000)  # far outside grid: clipped
    assert np.isfinite(vm.plane("105")).all()
    assert vm.plane("105").sum() == 2.0


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
