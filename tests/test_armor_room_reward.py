"""Reward + compass for armor room 205 vents → sun crest (pl78→pl79)."""

from __future__ import annotations

from pathlib import Path

import pytest

from re1_rl.armor_room_puzzle import (
    ARMOR_CABINET_XZ,
    ARMOR_STATUE_PROGRESS_BUDGET,
    ARMOR_STATUE_PROGRESS_STEP,
    ARMOR_VENTS,
    armor_statue_nav_target,
    armor_statue_progress_reward,
)
from re1_rl.planner_loyal import PlannerLoyalQueue, encode_planner_loyal_goal
from re1_rl.progress import ProgressTracker
from re1_rl.pushable import PUSH_GAME_STATE
from re1_rl.reward import compute_reward
from re1_rl.planner import WaypointPlanner
from tests.test_scaffolding import make_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
DOOR_VENT = ARMOR_VENTS[1]
FAR_VENT = ARMOR_VENTS[0]


def _sun_crest_queue() -> PlannerLoyalQueue:
    return PlannerLoyalQueue(
        {
            "chunk_id": "test_armor",
            "end_anchor_beat_id": "sun_crest",
            "steps": [
                {
                    "n": 1,
                    "op": "acquire",
                    "pickup_id": "205:sun_crest:1",
                    "room_id": "205",
                    "beat_id": "sun_crest",
                }
            ],
        }
    )


def _planner() -> WaypointPlanner:
    return WaypointPlanner(ROUTE, waypoints=["205"])


def _reward(prev, cur, queue, *, progress=None):
    return compute_reward(
        prev,
        cur,
        _planner(),
        progress=progress,
        planner_loyal_queue=queue,
        return_breakdown=True,
    )


def _armor_state(**kw):
    base = dict(
        room="205",
        inventory=[],
        inventory_slots=[],
        armor_puzzle_flag=0,
        armor_puzzle_ready=False,
        in_control=True,
    )
    base.update(kw)
    return make_state(**base)


def _pushing(**kw):
    return _armor_state(game_state=PUSH_GAME_STATE, **kw)


def test_nav_target_nearest_vent_from_door_spawn() -> None:
    q = _sun_crest_queue()
    state = _armor_state(x=16000, z=7300)
    assert armor_statue_nav_target(state, q) == (
        float(DOOR_VENT[0]),
        float(DOOR_VENT[1]),
    )


def test_nav_target_cabinet_when_both_seated() -> None:
    q = _sun_crest_queue()
    progress = ProgressTracker()
    progress.armor_vents_seated = [True, True]
    state = _armor_state(x=14000, z=7300)
    assert armor_statue_nav_target(state, q, progress) == (
        float(ARMOR_CABINET_XZ[0]),
        float(ARMOR_CABINET_XZ[1]),
    )


def test_nav_target_cabinet_when_flag_ready() -> None:
    q = _sun_crest_queue()
    state = _armor_state(
        x=14000,
        z=7300,
        armor_puzzle_flag=0x20,
        armor_puzzle_ready=True,
    )
    assert armor_statue_nav_target(state, q) == (
        float(ARMOR_CABINET_XZ[0]),
        float(ARMOR_CABINET_XZ[1]),
    )


def test_nav_target_off_when_crest_held() -> None:
    q = _sun_crest_queue()
    state = _armor_state(x=9700, z=7236, inventory=["sun_crest"])
    assert armor_statue_nav_target(state, q) is None


def test_progress_pays_half_when_closing() -> None:
    q = _sun_crest_queue()
    progress = ProgressTracker()
    far = _pushing(x=16000, z=7300)
    closer = _pushing(x=15000, z=7300)
    pay = armor_statue_progress_reward(far, closer, q, progress)
    assert pay == pytest.approx(ARMOR_STATUE_PROGRESS_STEP)


def test_progress_penalizes_retreat() -> None:
    q = _sun_crest_queue()
    progress = ProgressTracker()
    near = _pushing(x=14500, z=7300)
    far = _pushing(x=15800, z=7300)
    pay = armor_statue_progress_reward(near, far, q, progress)
    assert pay == pytest.approx(-ARMOR_STATUE_PROGRESS_STEP)


def test_progress_no_pay_when_not_pushing() -> None:
    q = _sun_crest_queue()
    far = _armor_state(x=16000, z=7300, player_anim=0x14)
    closer = _armor_state(x=15000, z=7300, player_anim=0x14)
    assert armor_statue_progress_reward(far, closer, q) == 0.0


def test_progress_target_switch_pays_zero() -> None:
    q = _sun_crest_queue()
    progress = ProgressTracker()
    # Arrive on the door-side vent while pushing — claim seats it, target flips.
    approach = _pushing(x=14400, z=7236)
    seated = _pushing(x=13985, z=7236)
    assert armor_statue_progress_reward(approach, seated, q, progress) == 0.0
    assert progress.armor_vents_seated[1] is True
    assert armor_statue_nav_target(seated, q, progress) == (
        float(FAR_VENT[0]),
        float(FAR_VENT[1]),
    )


def test_progress_in_compute_reward_breakdown() -> None:
    q = _sun_crest_queue()
    progress = ProgressTracker()
    progress._stagnation_frames = 400
    prev = _pushing(x=16000, z=7300)
    cur = _pushing(x=15000, z=7300, step=2)
    _total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["armor_statue_progress"] == pytest.approx(ARMOR_STATUE_PROGRESS_STEP)
    assert "dining_statue_progress" not in bd
    assert progress.stagnation_frames == 0


def test_progress_off_when_not_sun_crest_step() -> None:
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test_armor_traverse",
            "end_anchor_beat_id": "armor_room_enter",
            "steps": [
                {
                    "n": 1,
                    "op": "traverse",
                    "edge_id": "204->205",
                    "room_id": "204",
                    "beat_id": "armor_room_enter",
                }
            ],
        }
    )
    far = _pushing(x=16000, z=7300)
    closer = _pushing(x=15000, z=7300)
    assert armor_statue_progress_reward(far, closer, q) == 0.0


def test_divert_zeros_armor_progress() -> None:
    q = _sun_crest_queue()
    progress = ProgressTracker()
    prev = _pushing(x=16000, z=7300)
    cur = _pushing(room="204", x=15000, z=7300, step=2)
    _total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] < 0.0
    assert bd["armor_statue_progress"] == 0.0


def test_progress_approach_accumulates_before_seat() -> None:
    q = _sun_crest_queue()
    progress = ProgressTracker()
    # Stop outside the 420 seat radius so the target does not flip.
    xs = list(range(16000, 14500, -200))
    total = 0.0
    for a, b in zip(xs, xs[1:]):
        prev = _pushing(x=a, z=7300)
        cur = _pushing(x=b, z=7300)
        pay = armor_statue_progress_reward(prev, cur, q, progress)
        assert pay >= 0.0
        total += pay
    assert 2.0 <= total <= ARMOR_STATUE_PROGRESS_BUDGET
    assert progress.armor_vents_seated == [False, False]


def test_encode_goal_points_at_door_vent_not_cabinet() -> None:
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == "sun_crest")
    q.seek(idx)
    state = _armor_state(x=16000, z=7300, facing=0)
    goal = encode_planner_loyal_goal(
        encoder,
        graph,
        state,
        q,
        item_positions=ItemPositions(PROJECT_ROOT / "data" / "item_positions.json"),
    )
    vent = encoder._compass_to_xz(state, float(DOOR_VENT[0]), float(DOOR_VENT[1]))
    cabinet = encoder._compass_to_xz(
        state, float(ARMOR_CABINET_XZ[0]), float(ARMOR_CABINET_XZ[1])
    )
    assert goal[21] == pytest.approx(1.0)
    assert goal[5:10] == pytest.approx(vent)
    assert goal[5:10] != pytest.approx(cabinet)


def test_encode_goal_points_at_cabinet_after_flag() -> None:
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == "sun_crest")
    q.seek(idx)
    state = _armor_state(
        x=14000,
        z=7300,
        facing=0,
        armor_puzzle_flag=0x20,
        armor_puzzle_ready=True,
    )
    goal = encode_planner_loyal_goal(
        encoder,
        graph,
        state,
        q,
        item_positions=ItemPositions(PROJECT_ROOT / "data" / "item_positions.json"),
    )
    cabinet = encoder._compass_to_xz(
        state, float(ARMOR_CABINET_XZ[0]), float(ARMOR_CABINET_XZ[1])
    )
    assert goal[5:10] == pytest.approx(cabinet)
