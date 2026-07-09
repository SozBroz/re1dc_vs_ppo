"""Checkpoint-path reward guards are disabled in exploration mode."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import compute_reward
from re1_rl.room_graph import RoomGraph

ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"


def make_state(room, step=1, **kw):
    s = {"room_id": room, "x": 30000, "y": 0, "z": 7500, "facing": 0,
         "hp": 96, "cam_id": 0, "character_id": 1, "in_control": True,
         "inventory": [], "dead": False, "step": step}
    s.update(kw)
    return s


def test_no_waypoint_or_wrong_room_shaping():
    g = RoomGraph(DOORS)
    planner = WaypointPlanner(ROUTE, route_steps=[6])
    progress = ProgressTracker()
    _, bd = compute_reward(
        make_state("106", step=1),
        make_state("201", step=2),
        planner,
        progress=progress,
        graph=g,
        return_breakdown=True,
    )
    assert bd["waypoint"] == 0.0
    assert bd["wrong_room"] == 0.0
    assert bd["new_room"] > 0.0


def test_unmapped_goal_does_not_fine():
    g = RoomGraph(DOORS)
    planner = WaypointPlanner(ROUTE, waypoints=["119"])
    progress = ProgressTracker()
    _, bd = compute_reward(
        make_state("106", step=1),
        make_state("107", step=2),
        planner,
        progress=progress,
        graph=g,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == 0.0


def test_offroute_room_only_pays_exploration_bonus():
    g = RoomGraph(DOORS)
    planner = WaypointPlanner(ROUTE, waypoints=["106"])
    progress = ProgressTracker()
    _, bd = compute_reward(
        make_state("105", step=1),
        make_state("7FF", step=2),
        planner,
        progress=progress,
        graph=g,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == 0.0
    assert bd["new_room"] > 0.0
