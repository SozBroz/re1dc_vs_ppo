"""Offline tests: key-item TODO, ever-held tracking, per-room remaining counts,
and their goal-obs wiring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.item_todo import ItemTracker, RoomItems, build_item_todo
from re1_rl.obs_encoder import GOAL_FIELDS, ObsEncoder
from re1_rl.planner import WaypointPlanner
from re1_rl.room_graph import RoomGraph

ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
ROOMS = PROJECT_ROOT / "data" / "rooms.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"

GOAL_IDX = {name: i for i, (name, _) in enumerate(GOAL_FIELDS)}


def test_build_item_todo_from_route():
    todo = build_item_todo(ROUTE)
    assert len(todo) >= 30
    items = [t.item for t in todo]
    # route says "wooden_emblem"; canonical ITEM_IDS name is "emblem"
    assert "emblem" in items
    assert items.index("emblem") < items.index("armor_key")
    armor = next(t for t in todo if t.item == "armor_key")
    assert "chemical" in armor.required_items


def test_tracker_ever_held_survives_banking():
    tracker = ItemTracker(build_item_todo(ROUTE))
    # RAM decode may surface the alias or the canonical name; both normalize
    new = tracker.update([("beretta", 15), ("wooden_emblem", 1)])
    assert new == {"beretta", "emblem"}
    # bank the emblem (drops from inventory) -> still acquired
    assert tracker.update([("beretta", 15)]) == set()
    assert "emblem" in tracker.ever_held
    done, total = tracker.progress()
    assert done == 1
    # re-grab is NOT new
    assert tracker.update([("beretta", 15), ("emblem", 1)]) == set()
    checklist = tracker.format_checklist()
    assert "[x]" in checklist and "emblem" in checklist


def test_room_items_remaining(tmp_path):
    table = {
        "_meta": {"source": "test"},
        "105": {"room_name": "DINING ROOM", "items": [
            {"name": "emblem", "item_id": 31, "count": 1, "key_item": True},
            {"name": "green_herb", "item_id": 68, "count": 2, "key_item": False},
        ]},
    }
    p = tmp_path / "room_items.json"
    p.write_text(json.dumps(table), encoding="utf-8")
    ri = RoomItems(p)
    assert ri.loaded
    assert ri.remaining_in_room("105", set()) == 3
    assert ri.key_items_remaining_in_room("105", set()) == 1
    assert ri.remaining_in_room("105", {"emblem"}) == 2
    assert ri.key_items_remaining_in_room("105", {"emblem"}) == 0
    # unknown room / missing file degrade to 0 / not loaded
    assert ri.remaining_in_room("999", set()) == 0
    assert not RoomItems(tmp_path / "missing.json").loaded


def test_gated_items_excluded_until_requirements_held(tmp_path):
    table = {"10F": {"room_name": "BAR", "items": [
        {"name": "gold_emblem", "item_id": 32, "count": 1, "key_item": True,
         "gate": {"type": "item", "requires": ["wooden_emblem"],
                  "notes": "swap emblem at the alcove"}},
        {"name": "music_notes", "item_id": 35, "count": 1, "key_item": True},
        {"name": "shotgun", "item_id": 3, "count": 1, "key_item": False,
         "gate": {"type": "trap", "requires": [], "notes": "ceiling trap"}},
        {"name": "star_crest", "item_id": 45, "count": 1, "key_item": True,
         "gate": {"type": "event", "requires": [], "notes": "flag we can't track"}},
    ]}}
    p = tmp_path / "room_items.json"
    p.write_text(json.dumps(table), encoding="utf-8")
    ri = RoomItems(p)

    # gate unmet: gold_emblem hidden; trap item counts; untrackable event hidden
    assert ri.remaining_in_room("10F", set()) == 2  # music_notes + shotgun
    assert ri.key_items_remaining_in_room("10F", set()) == 1  # music_notes
    # "come back later" marker counts both locked items
    assert ri.gated_in_room("10F", set()) == 2  # gold_emblem + star_crest
    # requirement satisfied (alias-normalized): gold_emblem now counts
    held = {"emblem"}
    assert ri.remaining_in_room("10F", held) == 3
    assert ri.key_items_remaining_in_room("10F", held) == 2
    assert ri.gated_in_room("10F", held) == 1  # star_crest event still locked


def test_goal_obs_item_fields(tmp_path):
    table = {"105": {"room_name": "DINING ROOM", "items": [
        {"name": "wooden_emblem", "item_id": 31, "count": 1, "key_item": True},
    ]}}
    p = tmp_path / "room_items.json"
    p.write_text(json.dumps(table), encoding="utf-8")
    ri = RoomItems(p)

    graph = RoomGraph(DOORS)
    enc = ObsEncoder(ROOMS, graph)
    planner = WaypointPlanner(ROUTE, waypoints=["106"])
    tracker = ItemTracker(build_item_todo(ROUTE))
    state = {"room_id": "105", "x": 30000, "z": 7500, "facing": 0, "hp": 96,
             "inventory": [], "cam_id": 0, "character_id": 1}

    goal = enc.encode_goal(state, planner, item_tracker=tracker, room_items=ri)
    assert np.all(goal == 0.0)

    tracker.update([("wooden_emblem", 1)])
    goal2 = enc.encode_goal(state, planner, item_tracker=tracker, room_items=ri)
    assert np.all(goal2 == 0.0)


def test_decode_inventory():
    from re1_rl.memory_map import decode_inventory

    # slot u16 little-endian: low byte item_id, high byte qty
    ram = {"inv_slot_0": 0x0001, "inv_slot_1": 0x0F02, "inv_slot_2": 0x0141,
           "inv_slot_3": 0, "inv_slot_4": 0, "inv_slot_5": 0,
           "inv_slot_6": 0, "inv_slot_7": 0}
    inv = decode_inventory(ram)
    assert inv == [("knife", 0), ("beretta", 15), ("first_aid_spray_alt", 1)]


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
