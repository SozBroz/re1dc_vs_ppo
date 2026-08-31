"""Armor room 205: exact vent helper cells plus shove-only shaping."""

from __future__ import annotations

from pathlib import Path

import pytest

from re1_rl.armor_room_puzzle import (
    ARMOR_BUTTON_XZ,
    ARMOR_EAST_APPROACH_XZ,
    ARMOR_EAST_PUSH_ENDPOINT_XZ,
    ARMOR_EAST_SCRIPT_TARGET,
    ARMOR_WEST_APPROACH_XZ,
    ARMOR_WEST_PUSH_ENDPOINT_XZ,
    ARMOR_WEST_SCRIPT_TARGET,
    armor_statue_progress_reward,
    armor_stable_statues_seated,
    armor_vent_step_complete,
)
from re1_rl.planner import WaypointPlanner
from re1_rl.planner_loyal import PlannerLoyalQueue, encode_planner_loyal_goal
from re1_rl.progress import ProgressTracker
from re1_rl.pushable import PUSH_GAME_STATE
from re1_rl.reward import compute_reward
from tests.test_scaffolding import make_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"


def _planner() -> WaypointPlanner:
    return WaypointPlanner(ROUTE, waypoints=["205"])


def test_live_chunk_restores_two_strict_vent_helpers() -> None:
    q = PlannerLoyalQueue()
    beats = [s.get("beat_id") for s in q._steps[-4:]]
    assert beats == [
        "armor_room_enter",
        "armor_vent_door",
        "armor_vent_far",
        "sun_crest",
    ]
    assert q._steps[-1]["pickup_id"] == "205:sun_crest:1"
    assert q._steps[-3]["n"] == 74
    assert q._steps[-2]["n"] == 75
    assert q._steps[-1]["n"] == 76


def test_east_shove_toward_pays_and_away_is_punished() -> None:
    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_door"
    )
    q.seek(idx)
    progress = ProgressTracker()
    prev = _armor_state(
        x=ARMOR_EAST_APPROACH_XZ[0],
        z=ARMOR_EAST_APPROACH_XZ[1],
        game_state=PUSH_GAME_STATE,
    )
    toward = _armor_state(
        x=ARMOR_EAST_APPROACH_XZ[0],
        z=ARMOR_EAST_APPROACH_XZ[1] + 200,
        game_state=PUSH_GAME_STATE,
        step=2,
    )
    away = _armor_state(
        x=ARMOR_EAST_APPROACH_XZ[0],
        z=ARMOR_EAST_APPROACH_XZ[1] - 200,
        game_state=PUSH_GAME_STATE,
        step=2,
    )
    assert armor_statue_progress_reward(prev, toward, q, progress) > 0.0
    assert armor_statue_progress_reward(prev, away, q, progress) < 0.0
    _total, bd = compute_reward(
        prev,
        toward,
        _planner(),
        progress=progress,
        planner_loyal_queue=q,
        return_breakdown=True,
    )
    assert bd["armor_statue_progress"] > 0.0


def test_shaping_requires_active_push_and_current_vent_step() -> None:
    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_door"
    )
    q.seek(idx)
    prev = _armor_state(x=14000, z=5400)
    cur = _armor_state(x=14000, z=5600)
    assert armor_statue_progress_reward(prev, cur, q) == 0.0
    q.seek(idx + 2)
    prev["game_state"] = PUSH_GAME_STATE
    cur["game_state"] = PUSH_GAME_STATE
    assert armor_statue_progress_reward(prev, cur, q) == 0.0


def _goal(state: dict, beat_id: str) -> tuple[object, object]:
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == beat_id)
    q.seek(idx)
    items = ItemPositions(PROJECT_ROOT / "data" / "item_positions.json")
    goal = encode_planner_loyal_goal(
        encoder, graph, state, q, item_positions=items
    )
    return goal, encoder


def _armor_state(**overrides: object) -> dict:
    state = make_state(
        room="205",
        x=16000,
        z=7300,
        facing=0,
        in_control=True,
        armor_east_statue_x=13155,
        armor_east_statue_z=704,
        armor_west_statue_x=6949,
        armor_west_statue_z=3864,
    )
    state.update(overrides)
    return state


def _assert_compass(
    state: dict,
    target: tuple[int, int],
    beat_id: str,
) -> None:
    goal, encoder = _goal(state, beat_id)
    assert goal[21] == 1.0
    want = encoder._compass_to_xz(state, float(target[0]), float(target[1]))
    assert goal[5:10] == pytest.approx(want)


def test_crest_goal_guides_to_east_approach_then_push_endpoint() -> None:
    _assert_compass(
        _armor_state(), ARMOR_EAST_APPROACH_XZ, "armor_vent_door"
    )
    _assert_compass(
        _armor_state(x=ARMOR_EAST_APPROACH_XZ[0], z=ARMOR_EAST_APPROACH_XZ[1]),
        ARMOR_EAST_PUSH_ENDPOINT_XZ,
        "armor_vent_door",
    )


def test_crest_goal_advances_to_west_only_after_stable_east_target() -> None:
    state = _armor_state(
        armor_east_statue_x=ARMOR_EAST_SCRIPT_TARGET[0],
        armor_east_statue_z=ARMOR_EAST_SCRIPT_TARGET[1],
    )
    assert armor_stable_statues_seated(state) == (True, False)
    _assert_compass(state, ARMOR_WEST_APPROACH_XZ, "armor_vent_far")
    state.update(x=ARMOR_WEST_APPROACH_XZ[0], z=ARMOR_WEST_APPROACH_XZ[1])
    _assert_compass(state, ARMOR_WEST_PUSH_ENDPOINT_XZ, "armor_vent_far")


def test_crest_goal_points_to_button_after_both_stable_targets() -> None:
    state = _armor_state(
        armor_east_statue_x=ARMOR_EAST_SCRIPT_TARGET[0],
        armor_east_statue_z=ARMOR_EAST_SCRIPT_TARGET[1],
        armor_west_statue_x=ARMOR_WEST_SCRIPT_TARGET[0],
        armor_west_statue_z=ARMOR_WEST_SCRIPT_TARGET[1] + 1,
    )
    assert armor_stable_statues_seated(state) == (True, True)
    _assert_compass(state, ARMOR_BUTTON_XZ, "sun_crest")


def test_pl80_gate_rejects_either_statue_alone_and_requires_both() -> None:
    step = {"beat_id": "armor_vent_far"}
    east_only = _armor_state(
        armor_east_statue_x=ARMOR_EAST_SCRIPT_TARGET[0],
        armor_east_statue_z=ARMOR_EAST_SCRIPT_TARGET[1],
    )
    west_only = _armor_state(
        armor_west_statue_x=ARMOR_WEST_SCRIPT_TARGET[0],
        armor_west_statue_z=ARMOR_WEST_SCRIPT_TARGET[1],
    )
    both = dict(east_only)
    both.update(
        armor_west_statue_x=ARMOR_WEST_SCRIPT_TARGET[0],
        armor_west_statue_z=ARMOR_WEST_SCRIPT_TARGET[1],
    )
    flagged_but_unseated = _armor_state(armor_puzzle_flag=0x20)
    assert armor_vent_step_complete(step, east_only) is False
    assert armor_vent_step_complete(step, west_only) is False
    assert armor_vent_step_complete(step, flagged_but_unseated) is False
    assert armor_vent_step_complete(step, both) is True


def test_pl79_gate_requires_exact_east_target() -> None:
    step = {"beat_id": "armor_vent_door"}
    west_only = _armor_state(
        armor_west_statue_x=ARMOR_WEST_SCRIPT_TARGET[0],
        armor_west_statue_z=ARMOR_WEST_SCRIPT_TARGET[1],
    )
    east = _armor_state(
        armor_east_statue_x=ARMOR_EAST_SCRIPT_TARGET[0],
        armor_east_statue_z=ARMOR_EAST_SCRIPT_TARGET[1] + 8,
    )
    east_outside_tolerance = dict(east)
    east_outside_tolerance["armor_east_statue_z"] += 1
    assert armor_vent_step_complete(step, west_only) is False
    assert armor_vent_step_complete(step, east) is True
    assert armor_vent_step_complete(step, east_outside_tolerance) is False


def test_pl80_transition_advances_only_with_both_statues_seated() -> None:
    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_far"
    )
    q.seek(idx)
    east_only = _armor_state(
        armor_east_statue_x=ARMOR_EAST_SCRIPT_TARGET[0],
        armor_east_statue_z=ARMOR_EAST_SCRIPT_TARGET[1],
    )
    result = q.evaluate_transition(prev_state=east_only, state=east_only)
    assert result["step_success"] is False
    assert q.current and q.current["beat_id"] == "armor_vent_far"

    both = dict(east_only)
    both.update(
        armor_west_statue_x=ARMOR_WEST_SCRIPT_TARGET[0],
        armor_west_statue_z=ARMOR_WEST_SCRIPT_TARGET[1],
    )
    result = q.evaluate_transition(prev_state=east_only, state=both)
    assert result["step_success"] is True
    assert q.current and q.current["beat_id"] == "sun_crest"


def test_crest_goal_points_to_crest_after_puzzle_flag() -> None:
    from re1_rl.spatial_encoder import ItemPositions

    state = _armor_state(armor_puzzle_flag=0x20)
    goal, encoder = _goal(state, "sun_crest")
    items = ItemPositions(PROJECT_ROOT / "data" / "item_positions.json")
    target = items.get("205", "sun_crest")
    assert target is not None
    assert goal[21] == 1.0
    want = encoder._compass_to_xz(state, float(target[0]), float(target[1]))
    assert goal[5:10] == pytest.approx(want)
