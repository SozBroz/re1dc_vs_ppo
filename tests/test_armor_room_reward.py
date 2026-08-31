"""Reward + compass for armor room 205 vents → sun crest (pl79/pl80/pl81)."""

from __future__ import annotations

from pathlib import Path

import pytest

from re1_rl.armor_room_puzzle import (
    ARMOR_CABINET_XZ,
    ARMOR_STATUE_PROGRESS_BUDGET,
    ARMOR_STATUE_PROGRESS_STEP,
    ARMOR_STATUE_REST,
    ARMOR_VENT_DOOR,
    ARMOR_VENT_FAR,
    ARMOR_VENTS,
    armor_statue_nav_target,
    armor_statue_progress_reward,
    armor_vent_step_complete,
    encode_armor_statue_compass,
)
from re1_rl.planner import WaypointPlanner
from re1_rl.planner_loyal import PlannerLoyalQueue, encode_planner_loyal_goal
from re1_rl.progress import ProgressTracker
from re1_rl.pushable import PUSH_GAME_STATE
from re1_rl.reward import compute_reward
from tests.test_scaffolding import make_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
DOOR_VENT = ARMOR_VENTS[0]
FAR_VENT = ARMOR_VENTS[1]


def _armor_chunk_queue() -> PlannerLoyalQueue:
    return PlannerLoyalQueue(
        {
            "chunk_id": "test_armor",
            "end_anchor_beat_id": "sun_crest",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "armor_vent_door",
                    "room_id": "205",
                    "beat_id": "armor_vent_door",
                },
                {
                    "n": 2,
                    "op": "do_puzzle",
                    "site_id": "armor_vent_far",
                    "room_id": "205",
                    "beat_id": "armor_vent_far",
                },
                {
                    "n": 3,
                    "op": "acquire",
                    "pickup_id": "205:sun_crest:1",
                    "room_id": "205",
                    "beat_id": "sun_crest",
                },
            ],
        }
    )


def _door_queue() -> PlannerLoyalQueue:
    return _armor_chunk_queue()


def _far_queue() -> PlannerLoyalQueue:
    q = _armor_chunk_queue()
    q.seek(1)
    return q


def _sun_crest_queue() -> PlannerLoyalQueue:
    q = _armor_chunk_queue()
    q.seek(2)
    return q


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


def test_vent_order_is_door_then_far() -> None:
    assert ARMOR_VENTS == (ARMOR_VENT_DOOR, ARMOR_VENT_FAR)
    assert DOOR_VENT == (13892, 6370)
    assert FAR_VENT == (5258, 8152)


def test_nav_target_door_rest_until_pushing() -> None:
    q = _door_queue()
    near_far = _armor_state(x=6000, z=7300)
    assert armor_statue_nav_target(near_far, q) == (
        float(ARMOR_STATUE_REST[0][0]),
        float(ARMOR_STATUE_REST[0][1]),
    )
    shoving = _pushing(x=13696, z=7300)
    assert armor_statue_nav_target(shoving, q) == (
        float(DOOR_VENT[0]),
        float(DOOR_VENT[1]),
    )


def test_nav_target_far_rest_until_pushing() -> None:
    q = _far_queue()
    at_door = _armor_state(x=16000, z=7300)
    assert armor_statue_nav_target(at_door, q) == (
        float(ARMOR_STATUE_REST[1][0]),
        float(ARMOR_STATUE_REST[1][1]),
    )
    shoving = _pushing(x=5424, z=7300)
    assert armor_statue_nav_target(shoving, q) == (
        float(FAR_VENT[0]),
        float(FAR_VENT[1]),
    )


def test_nav_target_cabinet_on_crest_step() -> None:
    q = _sun_crest_queue()
    state = _armor_state(x=14000, z=7300)
    assert armor_statue_nav_target(state, q) == (
        float(ARMOR_CABINET_XZ[0]),
        float(ARMOR_CABINET_XZ[1]),
    )


def test_nav_target_off_when_crest_held() -> None:
    q = _door_queue()
    state = _armor_state(x=9700, z=7236, inventory=["sun_crest"])
    assert armor_statue_nav_target(state, q) is None


def test_progress_pays_half_when_closing() -> None:
    q = _door_queue()
    progress = ProgressTracker()
    far = _pushing(x=16000, z=7300)
    closer = _pushing(x=15000, z=7300)
    pay = armor_statue_progress_reward(far, closer, q, progress)
    assert pay == pytest.approx(ARMOR_STATUE_PROGRESS_STEP)


def test_progress_penalizes_retreat() -> None:
    q = _door_queue()
    progress = ProgressTracker()
    near = _pushing(x=14500, z=7300)
    far = _pushing(x=15800, z=7300)
    pay = armor_statue_progress_reward(near, far, q, progress)
    assert pay == pytest.approx(-ARMOR_STATUE_PROGRESS_STEP)


def test_progress_no_pay_when_not_pushing() -> None:
    q = _door_queue()
    far = _armor_state(x=16000, z=7300, player_anim=0x14)
    closer = _armor_state(x=15000, z=7300, player_anim=0x14)
    assert armor_statue_progress_reward(far, closer, q) == 0.0


def test_progress_no_pay_on_crest_acquire_step() -> None:
    q = _sun_crest_queue()
    far = _pushing(x=16000, z=7300)
    closer = _pushing(x=15000, z=7300)
    assert armor_statue_progress_reward(far, closer, q) == 0.0


def test_progress_in_compute_reward_breakdown() -> None:
    q = _door_queue()
    progress = ProgressTracker()
    progress._stagnation_frames = 400
    prev = _pushing(x=16000, z=7300)
    cur = _pushing(x=15000, z=7300, step=2)
    _total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["armor_statue_progress"] == pytest.approx(ARMOR_STATUE_PROGRESS_STEP)
    assert "dining_statue_progress" not in bd
    assert progress.stagnation_frames == 0


def test_progress_off_when_not_vent_step() -> None:
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
    q = _door_queue()
    progress = ProgressTracker()
    prev = _pushing(x=16000, z=7300)
    cur = _pushing(room="204", x=15000, z=7300, step=2)
    _total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] < 0.0
    assert bd["armor_statue_progress"] == 0.0


def test_progress_approach_accumulates_before_seat() -> None:
    q = _door_queue()
    progress = ProgressTracker()
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


def test_pushing_at_door_pedestal_does_not_complete() -> None:
    q = _door_queue()
    rest = _pushing(x=ARMOR_STATUE_REST[0][0], z=ARMOR_STATUE_REST[0][1])
    assert armor_vent_step_complete(q.current, rest) is False
    result = q.evaluate_transition(prev_state=rest, state=rest)
    assert result["step_success"] is False
    assert q.current is not None
    assert q.current["beat_id"] == "armor_vent_door"


def test_standing_on_door_vent_does_not_complete() -> None:
    """False pl79: Jill on the empty door grate, flag still 0."""
    q = _door_queue()
    prev = _pushing(x=14400, z=7236)
    cur = _pushing(x=14067, z=7118, armor_statue_x=14008, armor_statue_z=7231)
    assert armor_vent_step_complete(q.current, cur) is False
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is False
    assert q.current is not None
    assert q.current["beat_id"] == "armor_vent_door"


def test_statue_on_door_drain_completes_pl() -> None:
    """QS1-class seat still completes after baking the authored pl79 oracle."""
    q = _door_queue()
    prev = _pushing(x=14047, z=6118, armor_statue_x=13829, armor_statue_z=6200)
    cur = _pushing(x=14083, z=6351, armor_statue_x=13935, armor_statue_z=6347)
    assert armor_vent_step_complete(q.current, cur) is True
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is True
    assert q.current is not None
    assert q.current["beat_id"] == "armor_vent_far"


def test_authored_pl79_pose_completes_and_advances() -> None:
    """Worker mint: the human pl79 RAM pose must fire step_success."""
    q = _door_queue()
    cur = _armor_state(x=14080, z=6468, armor_statue_x=13892, armor_statue_z=6370)
    assert armor_vent_step_complete(q.current, cur) is True
    result = q.evaluate_transition(prev_state=cur, state=cur)
    assert result["step_success"] is True
    assert q.current["beat_id"] == "armor_vent_far"


def test_authored_pl79_pose_pays_planner_step_success() -> None:
    """Capture keys on this pulse — freeze then mint pl79."""
    q = _door_queue()
    prev = _armor_state(x=14080, z=6468, armor_statue_x=13892, armor_statue_z=6370)
    cur = _armor_state(
        x=14080, z=6468, armor_statue_x=13892, armor_statue_z=6370, step=2
    )
    _total, bd = _reward(prev, cur, q)
    assert bd["planner_step_success"] > 0.0
    assert bd["checkpoint_success"] > 0.0


def test_statue_on_door_grate_but_jill_far_does_not_complete() -> None:
    """Seat requires Jill beside the statue, not across the room."""
    q = _door_queue()
    cur = _pushing(x=16000, z=7300, armor_statue_x=13892, armor_statue_z=6370)
    assert armor_vent_step_complete(q.current, cur) is False


def test_rdt_aot_overshoot_does_not_complete_door() -> None:
    """Statue at the old RDT AOT (13985, 7236) has already passed the drain."""
    q = _door_queue()
    cur = _pushing(x=14047, z=6718, armor_statue_x=14008, armor_statue_z=7231)
    assert armor_vent_step_complete(q.current, cur) is False


def test_idle_helper_near_jill_does_not_complete() -> None:
    """After release the live slot snaps ~300 from Jill — not a seat."""
    q = _door_queue()
    idle = _armor_state(
        x=14000,
        z=7000,
        armor_statue_x=13985,
        armor_statue_z=7236,
    )
    assert armor_vent_step_complete(q.current, idle) is False


def test_statue_on_door_drain_does_not_complete_far_step() -> None:
    q = _far_queue()
    cur = _pushing(x=14047, z=6718, armor_statue_x=14008, armor_statue_z=7231)
    assert armor_vent_step_complete(q.current, cur) is False


def test_statue_on_far_drain_completes_far_pl() -> None:
    """Prior live far seat still completes after baking authored pl80."""
    q = _far_queue()
    prev = _pushing(x=5050, z=7900, armor_statue_x=5236, armor_statue_z=8000)
    cur = _armor_state(x=5050, z=8008, armor_statue_x=5236, armor_statue_z=8102)
    assert armor_vent_step_complete(q.current, cur) is True
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is True
    assert q.current is not None
    assert q.current["beat_id"] == "sun_crest"


def test_authored_pl80_pose_completes_and_advances() -> None:
    """Worker mint: the human pl80 RAM pose must fire step_success."""
    q = _far_queue()
    cur = _armor_state(x=5072, z=8058, armor_statue_x=5258, armor_statue_z=8152)
    assert armor_vent_step_complete(q.current, cur) is True
    result = q.evaluate_transition(prev_state=cur, state=cur)
    assert result["step_success"] is True
    assert q.current["beat_id"] == "sun_crest"


def test_far_rdt_aot_does_not_complete() -> None:
    """RDT far grate (5135, 7236) is 874 from the QS5 seat."""
    q = _far_queue()
    cur = _pushing(x=5135, z=7236, armor_statue_x=5135, armor_statue_z=7236)
    assert armor_vent_step_complete(q.current, cur) is False


def test_far_slot_z_while_jill_south_does_not_complete() -> None:
    """Same undershoot class as the door: slot in band, Jill still 200 south."""
    q = _far_queue()
    cur = _pushing(x=4827, z=7800, armor_statue_x=5013, armor_statue_z=8102)
    assert armor_vent_step_complete(q.current, cur) is False


def test_false_pl80_helper_near_dock_does_not_complete() -> None:
    """Minted pl80: Jill at the far dock, live slot on a helper, pillar unmoved."""
    q = _far_queue()
    cur = _armor_state(x=4841, z=8063, armor_statue_x=4777, armor_statue_z=7861)
    assert armor_vent_step_complete(q.current, cur) is False


def test_false_pl80_jill_on_empty_west_grate_does_not_complete() -> None:
    """Remint: Jill on the empty west grate, far pillar not on QS5."""
    q = _far_queue()
    cur = _armor_state(x=4605, z=7724, armor_statue_x=4806, armor_statue_z=7678)
    assert armor_vent_step_complete(q.current, cur) is False


def test_jill_standing_on_far_seat_does_not_complete() -> None:
    """Statue-on-grate only — Jill standing on the QS5 grate is not a seat."""
    q = _far_queue()
    cur = _armor_state(x=5013, z=8102, armor_statue_x=5013, armor_statue_z=8102)
    assert armor_vent_step_complete(q.current, cur) is False


def test_door_statue_slot_does_not_complete_far_step() -> None:
    """The east pedestal is the other statue — not pl80."""
    q = _far_queue()
    cur = _armor_state(x=4827, z=8008, armor_statue_x=13936, armor_statue_z=6347)
    assert armor_vent_step_complete(q.current, cur) is False


def test_standing_on_far_vent_does_not_complete_far_step() -> None:
    q = _far_queue()
    prev = _pushing(x=5600, z=7236)
    cur = _pushing(x=5135, z=7236)
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is False
    assert q.current is not None
    assert q.current["beat_id"] == "armor_vent_far"


def test_standing_on_far_vent_does_not_complete_door_step() -> None:
    q = _door_queue()
    prev = _pushing(x=5600, z=7236)
    cur = _pushing(x=5135, z=7236)
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is False
    assert q.current is not None
    assert q.current["beat_id"] == "armor_vent_door"


def test_bad_pl79_remint_does_not_complete() -> None:
    """Jill 13963,6279 / slot 14181,6495 is 286 off the QS1 seat."""
    q = _door_queue()
    cur = _pushing(x=13963, z=6279, armor_statue_x=14181, armor_statue_z=6495)
    assert armor_vent_step_complete(q.current, cur) is False


def test_idle_qs1_pose_completes() -> None:
    q = _door_queue()
    cur = _armor_state(x=14083, z=6351, armor_statue_x=13935, armor_statue_z=6347)
    assert armor_vent_step_complete(q.current, cur) is True


def test_compass_ahead_when_facing_door_grate() -> None:
    """RE1 1024 is -Z. Door grate is north of the aisle rest."""
    q = _door_queue()
    state = _pushing(x=13696, z=7300, facing=1024)
    compass = encode_armor_statue_compass(state, q)
    assert compass is not None
    assert compass[4] == pytest.approx(1.0, abs=0.15)


def test_flag_ready_skips_both_vents_to_crest() -> None:
    q = _door_queue()
    state = _armor_state(armor_puzzle_flag=0x20, armor_puzzle_ready=True)
    result = q.evaluate_transition(prev_state=state, state=state)
    assert result["step_success"] is False
    assert q.current is not None
    assert q.current["beat_id"] == "sun_crest"


def test_live_chunk_has_door_then_far_then_crest() -> None:
    q = PlannerLoyalQueue()
    beats = [s.get("beat_id") for s in q._steps[-4:]]
    assert beats == [
        "armor_room_enter",
        "armor_vent_door",
        "armor_vent_far",
        "sun_crest",
    ]
    assert q._steps[-1]["pickup_id"] == "205:sun_crest:1"


def test_encode_goal_points_at_door_vent_not_cabinet() -> None:
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_door")
    q.seek(idx)
    state = _armor_state(x=16000, z=7300, facing=0)
    goal = encode_planner_loyal_goal(
        encoder,
        graph,
        state,
        q,
        item_positions=ItemPositions(PROJECT_ROOT / "data" / "item_positions.json"),
    )
    rest = encoder._compass_to_xz(
        state, float(ARMOR_STATUE_REST[0][0]), float(ARMOR_STATUE_REST[0][1])
    )
    vent = encoder._compass_to_xz(state, float(DOOR_VENT[0]), float(DOOR_VENT[1]))
    cabinet = encoder._compass_to_xz(
        state, float(ARMOR_CABINET_XZ[0]), float(ARMOR_CABINET_XZ[1])
    )
    assert goal[21] == pytest.approx(1.0)
    assert goal[5:10] == pytest.approx(rest)
    assert goal[5:10] != pytest.approx(vent)
    assert goal[5:10] != pytest.approx(cabinet)


def test_encode_goal_points_at_door_vent_while_pushing() -> None:
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_door")
    q.seek(idx)
    state = _pushing(x=13696, z=7300, facing=0)
    goal = encode_planner_loyal_goal(
        encoder,
        graph,
        state,
        q,
        item_positions=ItemPositions(PROJECT_ROOT / "data" / "item_positions.json"),
    )
    vent = encoder._compass_to_xz(state, float(DOOR_VENT[0]), float(DOOR_VENT[1]))
    assert goal[5:10] == pytest.approx(vent)


def test_encode_goal_points_at_far_vent_on_second_cell() -> None:
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_far")
    q.seek(idx)
    state = _armor_state(x=16000, z=7300, facing=0)
    goal = encode_planner_loyal_goal(
        encoder,
        graph,
        state,
        q,
        item_positions=ItemPositions(PROJECT_ROOT / "data" / "item_positions.json"),
    )
    rest = encoder._compass_to_xz(
        state, float(ARMOR_STATUE_REST[1][0]), float(ARMOR_STATUE_REST[1][1])
    )
    far = encoder._compass_to_xz(state, float(FAR_VENT[0]), float(FAR_VENT[1]))
    assert goal[5:10] == pytest.approx(rest)
    assert goal[5:10] != pytest.approx(far)


def test_encode_goal_points_at_cabinet_on_crest_step() -> None:
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    idx = next(i for i, s in enumerate(q._steps) if s.get("beat_id") == "sun_crest")
    q.seek(idx)
    state = _armor_state(x=14000, z=7300, facing=0)
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
