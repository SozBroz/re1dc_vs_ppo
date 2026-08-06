"""Checkpoint-path wrong_room guards (all rails off-path = terminal -4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    WRONG_ROOM_TERMINAL_PENALTY,
    compute_reward,
)
from re1_rl.room_graph import RoomGraph

ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
YAWN_ROUTE = PROJECT_ROOT / "data" / "yawn_checkpoint_route.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"


def make_state(room, step=1, **kw):
    s = {
        "room_id": room,
        "x": 30000,
        "y": 0,
        "z": 7500,
        "facing": 0,
        "hp": 96,
        "cam_id": 0,
        "character_id": 1,
        "in_control": True,
        "inventory": [],
        "dead": False,
        "step": step,
    }
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


def test_rails_connected_detour_is_terminal_minus_four():
    """Any rails hop that does not get closer pays -4 and ends the episode."""
    g = RoomGraph(DOORS)
    planner = WaypointPlanner(ROUTE, waypoints=["104"])
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    progress.observe_cutscene("104:0:s0")

    _, bd = compute_reward(
        make_state("105", step=1),
        make_state("106", step=2),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )

    assert bd["wrong_room"] == WRONG_ROOM_TERMINAL_PENALTY
    assert bd["wrong_room"] == pytest.approx(-4.0)
    assert bd["new_room"] == 0.0
    assert progress.wrong_room_breached is True


def test_rails_shortest_path_step_is_not_wrong_room():
    g = RoomGraph(DOORS)
    planner = WaypointPlanner(ROUTE, waypoints=["104"])
    progress = ProgressTracker()
    progress.seed_spawn_room("105")

    _, bd = compute_reward(
        make_state("105", step=1),
        make_state("104", step=2),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )

    assert bd["wrong_room"] == 0.0


def _yawn_planner(checkpoint_id: str) -> WaypointPlanner:
    import json

    route = json.loads(YAWN_ROUTE.read_text(encoding="utf-8"))
    idx = next(i for i, row in enumerate(route) if row["checkpoint_id"] == checkpoint_id)
    return WaypointPlanner(
        YAWN_ROUTE,
        route_steps=list(range(1, len(route) + 1)),
        start_index=idx,
    )


def test_post_l_passage_detour_is_terminal_minus_four():
    """After L enter: wrong room pays -4, zeros new_room, marks episode terminal."""
    g = RoomGraph(DOORS)
    # Active objective = enter 108; detour 107 -> 106 is away from 108.
    planner = _yawn_planner("l_passage_enter_108")
    assert planner.current_objective()["checkpoint_id"] == "l_passage_enter_108"
    progress = ProgressTracker()
    progress.seed_spawn_room("107")

    _, bd = compute_reward(
        make_state("107", step=1),
        make_state("106", step=2),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )

    assert bd["wrong_room"] == WRONG_ROOM_TERMINAL_PENALTY
    assert bd["wrong_room"] == pytest.approx(-4.0)
    assert bd["new_room"] == 0.0
    assert progress.wrong_room_breached is True


def test_post_l_ammo_pickup_order_inside_108_is_not_wrong_room():
    """Staying in the checkpoint room never pays wrong_room (no item-order gate)."""
    g = RoomGraph(DOORS)
    planner = _yawn_planner("ammo_108")
    assert planner.current_objective()["checkpoint_id"] == "ammo_108"
    progress = ProgressTracker()
    progress.seed_spawn_room("108")

    _, bd = compute_reward(
        make_state("108", step=1),
        make_state("108", step=2, inventory=["handgun_bullets"]),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == 0.0
    assert progress.wrong_room_breached is False


def test_post_l_leave_checkpoint_target_is_terminal_wrong_room():
    """After L enter: leaving 108 while waypoint stays 108 is -4 + episode end."""
    g = RoomGraph(DOORS)
    planner = _yawn_planner("l_passage_enter_108")
    assert planner.next_waypoint_room() == "108"
    progress = ProgressTracker()
    progress.seed_spawn_room("108")

    _, bd = compute_reward(
        make_state("108", step=1),
        make_state("107", step=2),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == WRONG_ROOM_TERMINAL_PENALTY
    assert bd.get("retreat", 0.0) == 0.0
    assert progress.wrong_room_breached is True


def test_leave_checkpoint_target_is_always_terminal_wrong_room():
    """Leaving the target room is always -4 + episode end (no soft retreat)."""
    g = RoomGraph(DOORS)
    planner = WaypointPlanner(ROUTE, waypoints=["104"])
    assert planner.next_waypoint_room() == "104"
    progress = ProgressTracker()
    progress.seed_spawn_room("104")
    progress.observe_cutscene("104:0:s0")

    _, bd = compute_reward(
        make_state("104", step=1),
        make_state("105", step=2),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == WRONG_ROOM_TERMINAL_PENALTY
    assert bd.get("retreat", 0.0) == 0.0
    assert progress.wrong_room_breached is True
