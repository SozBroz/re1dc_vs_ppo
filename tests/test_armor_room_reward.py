"""Armor room 205: enter then sun crest. No vent helper cells or crumbs."""

from __future__ import annotations

from pathlib import Path

import pytest

from re1_rl.armor_room_puzzle import armor_statue_progress_reward
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


def test_live_chunk_is_enter_then_sun_crest() -> None:
    q = PlannerLoyalQueue()
    beats = [s.get("beat_id") for s in q._steps[-2:]]
    assert beats == ["armor_room_enter", "sun_crest"]
    assert q._steps[-1]["pickup_id"] == "205:sun_crest:1"
    assert q._steps[-1]["n"] == 74
    assert not any(
        s.get("beat_id") in {"armor_vent_door", "armor_vent_far"} for s in q._steps
    )


def test_shove_crumb_does_not_pay() -> None:
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == "sun_crest")
    q.seek(idx)
    progress = ProgressTracker()
    prev = make_state(
        room="205",
        x=16000,
        z=7300,
        game_state=PUSH_GAME_STATE,
        in_control=True,
    )
    cur = make_state(
        room="205",
        x=15000,
        z=7300,
        game_state=PUSH_GAME_STATE,
        in_control=True,
        step=2,
    )
    assert armor_statue_progress_reward(prev, cur, q, progress) == 0.0
    _total, bd = compute_reward(
        prev,
        cur,
        _planner(),
        progress=progress,
        planner_loyal_queue=q,
        return_breakdown=True,
    )
    assert bd.get("armor_statue_progress", 0.0) == 0.0


def test_encode_goal_on_crest_step_is_not_a_vent() -> None:
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == "sun_crest")
    q.seek(idx)
    state = make_state(room="205", x=16000, z=7300, facing=0, in_control=True)
    items = ItemPositions(PROJECT_ROOT / "data" / "item_positions.json")
    goal = encode_planner_loyal_goal(
        encoder, graph, state, q, item_positions=items
    )
    target = items.get("205", "sun_crest")
    assert target is not None
    assert goal[21] == 1.0
    want = encoder._compass_to_xz(state, float(target[0]), float(target[1]))
    assert goal[5:10] == pytest.approx(want)
