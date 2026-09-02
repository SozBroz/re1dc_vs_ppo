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
    ARMOR_WEST_LATERAL_APPROACH_XZ,
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
        **_statue_fields("east", (14035, 6190)),
    )
    toward = _armor_state(
        x=ARMOR_EAST_APPROACH_XZ[0],
        z=ARMOR_EAST_APPROACH_XZ[1] + 200,
        game_state=PUSH_GAME_STATE,
        step=2,
        **_statue_fields("east", (14035, 6390)),
    )
    away = _armor_state(
        x=ARMOR_EAST_APPROACH_XZ[0],
        z=ARMOR_EAST_APPROACH_XZ[1] - 200,
        game_state=PUSH_GAME_STATE,
        step=2,
        **_statue_fields("east", (14035, 5990)),
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
    prev = _armor_state(
        x=14000, z=5400, **_statue_fields("east", (14035, 6190))
    )
    cur = _armor_state(
        x=14000, z=5600, **_statue_fields("east", (14035, 6390))
    )
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


def _statue_fields(prefix: str, xz: tuple[int, int]) -> dict[str, int]:
    x, z = xz
    return {
        f"armor_{prefix}_statue_x": x,
        f"armor_{prefix}_statue_z": z,
        f"armor_{prefix}_statue_x_b": x,
        f"armor_{prefix}_statue_z_b": z,
        f"armor_{prefix}_statue_x_c": x,
        f"armor_{prefix}_statue_z_c": z,
    }


def _armor_state(**overrides: object) -> dict:
    state = make_state(
        room="205",
        x=16000,
        z=7300,
        facing=0,
        in_control=True,
        **_statue_fields("east", (14035, 6190)),
        **_statue_fields("west", (8795, 7886)),
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
    state = _armor_state(**_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET))
    assert armor_stable_statues_seated(state) == (True, False)
    _assert_compass(state, ARMOR_WEST_APPROACH_XZ, "armor_vent_far")
    state.update(x=ARMOR_WEST_APPROACH_XZ[0], z=ARMOR_WEST_APPROACH_XZ[1])
    _assert_compass(state, ARMOR_WEST_PUSH_ENDPOINT_XZ, "armor_vent_far")
    state.update(x=16000, z=7300)
    state.update(**_statue_fields("west", (8795, ARMOR_WEST_SCRIPT_TARGET[1])))
    _assert_compass(state, ARMOR_WEST_LATERAL_APPROACH_XZ, "armor_vent_far")


def test_crest_goal_points_to_button_after_both_stable_targets() -> None:
    state = _armor_state(
        **_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET),
        **_statue_fields(
            "west",
            (ARMOR_WEST_SCRIPT_TARGET[0], ARMOR_WEST_SCRIPT_TARGET[1] + 1),
        ),
    )
    assert armor_stable_statues_seated(state) == (True, True)
    _assert_compass(state, ARMOR_BUTTON_XZ, "sun_crest")


def test_pl80_gate_rejects_either_statue_alone_and_requires_both() -> None:
    step = {"beat_id": "armor_vent_far"}
    east_only = _armor_state(**_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET))
    west_only = _armor_state(**_statue_fields("west", ARMOR_WEST_SCRIPT_TARGET))
    both = dict(east_only)
    both.update(_statue_fields("west", ARMOR_WEST_SCRIPT_TARGET))
    flagged_but_unseated = _armor_state(armor_puzzle_flag=0x20)
    assert armor_vent_step_complete(step, east_only) is False
    assert armor_vent_step_complete(step, west_only) is False
    assert armor_vent_step_complete(step, flagged_but_unseated) is False
    assert armor_vent_step_complete(step, both) is True


def test_pl79_gate_requires_exact_east_target() -> None:
    step = {"beat_id": "armor_vent_door"}
    west_only = _armor_state(**_statue_fields("west", ARMOR_WEST_SCRIPT_TARGET))
    east = _armor_state(
        **_statue_fields(
            "east",
            (ARMOR_EAST_SCRIPT_TARGET[0], ARMOR_EAST_SCRIPT_TARGET[1] + 8),
        )
    )
    east_outside_tolerance = dict(east)
    for suffix in ("", "_b", "_c"):
        east_outside_tolerance[f"armor_east_statue_z{suffix}"] += 1
    assert armor_vent_step_complete(step, west_only) is False
    assert armor_vent_step_complete(step, east) is True
    assert armor_vent_step_complete(step, east_outside_tolerance) is False


def test_gate_rejects_mirror_mismatch_and_observed_false_mints() -> None:
    east_step = {"beat_id": "armor_vent_door"}
    west_step = {"beat_id": "armor_vent_far"}
    mismatched = _armor_state(**_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET))
    mismatched["armor_east_statue_z_b"] -= 50
    false_pl79 = _armor_state()
    false_pl80 = _armor_state(**_statue_fields("east", (14035, 5340)))
    assert armor_vent_step_complete(east_step, mismatched) is False
    assert armor_vent_step_complete(east_step, false_pl79) is False
    assert armor_vent_step_complete(west_step, false_pl80) is False


def test_pl79_still_rejects_one_shove_step_off_east() -> None:
    step = {"beat_id": "armor_vent_door"}
    base = _armor_state(**_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET))
    assert armor_vent_step_complete(step, base) is True
    for dx, dz in ((50, 0), (-50, 0), (0, 50), (0, -50)):
        near = dict(base)
        near.update(
            _statue_fields(
                "east",
                (ARMOR_EAST_SCRIPT_TARGET[0] + dx, ARMOR_EAST_SCRIPT_TARGET[1] + dz),
            )
        )
        assert armor_vent_step_complete(step, near) is False


def test_pl80_accepts_human_west_seat_box_and_rejects_outside() -> None:
    step = {"beat_id": "armor_vent_far"}
    base = _armor_state(
        **_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET),
        **_statue_fields("west", ARMOR_WEST_SCRIPT_TARGET),
    )
    assert armor_vent_step_complete(step, base) is True
    # Corners / extremes of the human-validated AABB.
    for xz in (
        (4845, 7086),
        (4845, 7336),
        (5195, 7086),
        (5195, 7336),
        (4895, 7336),
        (4945, 7136),
        (4895, 7086),
    ):
        near = dict(base)
        near.update(_statue_fields("west", xz))
        assert armor_vent_step_complete(step, near) is True, xz
    for xz in (
        (4844, 7186),
        (5196, 7186),
        (4895, 7085),
        (4895, 7337),
        (4695, 7336),
    ):
        far = dict(base)
        far.update(_statue_fields("west", xz))
        assert armor_vent_step_complete(step, far) is False, xz


def test_human_button_valid_west_seat_mints_pl80() -> None:
    """Live human: west (4945, 7136), button pressed, no gas — must mint pl80."""
    step = {"beat_id": "armor_vent_far"}
    both = _armor_state(
        **_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET),
        **_statue_fields("west", (4945, 7136)),
    )
    assert armor_vent_step_complete(step, both) is True
    assert armor_stable_statues_seated(both) == (True, True)


def test_pl80_transition_advances_only_with_both_statues_seated() -> None:
    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_far"
    )
    q.seek(idx)
    east_only = _armor_state(**_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET))
    result = q.evaluate_transition(prev_state=east_only, state=east_only)
    assert result["step_success"] is False
    assert q.current and q.current["beat_id"] == "armor_vent_far"

    both = dict(east_only)
    both.update(_statue_fields("west", ARMOR_WEST_SCRIPT_TARGET))
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


def test_inplace_push_west_on_east_step_is_ignored() -> None:
    from re1_rl.armor_room_puzzle import armor_inplace_statue_push_detected

    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_door"
    )
    q.seek(idx)
    prev = _armor_state(game_state=PUSH_GAME_STATE)
    cur = _armor_state(
        game_state=PUSH_GAME_STATE,
        **_statue_fields("west", (8795, 7836)),
    )
    assert armor_inplace_statue_push_detected(prev, cur, q) is False


def test_inplace_push_east_on_pl80_step_is_detected() -> None:
    from re1_rl.armor_room_puzzle import armor_inplace_statue_push_detected

    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_far"
    )
    q.seek(idx)
    prev = _armor_state(
        game_state=PUSH_GAME_STATE,
        **_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET),
    )
    cur = _armor_state(
        game_state=PUSH_GAME_STATE,
        **_statue_fields("east", (14035, 7290)),
        **_statue_fields("west", (8795, 7886)),
    )
    assert armor_inplace_statue_push_detected(prev, cur, q) is True


def test_target_statue_shove_toward_vent_is_not_inplace_breach() -> None:
    from re1_rl.armor_room_puzzle import armor_inplace_statue_push_detected

    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_far"
    )
    q.seek(idx)
    prev = _armor_state(
        game_state=PUSH_GAME_STATE,
        **_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET),
    )
    cur = _armor_state(
        game_state=PUSH_GAME_STATE,
        **_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET),
        **_statue_fields("west", (8795, 7836)),
    )
    assert armor_inplace_statue_push_detected(prev, cur, q) is False


def test_inplace_push_applies_terminal_penalty() -> None:
    from re1_rl.armor_room_puzzle import ARMOR_INPLACE_STATUE_PUSH_PENALTY

    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_far"
    )
    q.seek(idx)
    progress = ProgressTracker()
    prev = _armor_state(
        game_state=PUSH_GAME_STATE,
        **_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET),
    )
    cur = _armor_state(
        game_state=PUSH_GAME_STATE,
        **_statue_fields("east", (14035, 7290)),
    )
    _total, bd = compute_reward(
        prev,
        cur,
        _planner(),
        progress=progress,
        planner_loyal_queue=q,
        return_breakdown=True,
    )
    assert bd["armor_inplace_statue_push"] == pytest.approx(
        -ARMOR_INPLACE_STATUE_PUSH_PENALTY
    )
    assert progress.armor_inplace_statue_push_breached is True
    assert bd["armor_statue_progress"] == 0.0


def _far_queue() -> PlannerLoyalQueue:
    q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(q._steps) if s.get("beat_id") == "armor_vent_far"
    )
    q.seek(idx)
    return q


def _pl79_spawn_state(**overrides: object) -> dict:
    """Jill jammed against the seated east statue, facing it (live pl79 pose)."""
    state = _armor_state(
        x=14345,
        z=6518,
        facing=2048,
        **_statue_fields("east", ARMOR_EAST_SCRIPT_TARGET),
    )
    state.update(overrides)
    return state


def _far_leg_reward(prev: dict, cur: dict, q, progress) -> dict:
    _total, bd = compute_reward(
        prev,
        cur,
        _planner(),
        progress=progress,
        planner_loyal_queue=q,
        return_breakdown=True,
    )
    return bd


def test_approach_baseline_fixed_on_first_far_leg_step_only() -> None:
    q = _far_queue()
    progress = ProgressTracker()
    start = _pl79_spawn_state()
    toward = _pl79_spawn_state(x=start["x"] - 350, step=2)
    _far_leg_reward(start, toward, q, progress)
    ref = progress.armor_far_approach_reference
    assert ref is not None and ref > 5000.0
    _far_leg_reward(toward, _pl79_spawn_state(x=start["x"] - 700, step=3), q, progress)
    assert progress.armor_far_approach_reference == ref
    # Not baselined while the east leg is current.
    east_q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(east_q._steps) if s.get("beat_id") == "armor_vent_door"
    )
    east_q.seek(idx)
    fresh = ProgressTracker()
    bd = _far_leg_reward(_armor_state(), _armor_state(x=15800, step=2), east_q, fresh)
    assert fresh.armor_far_approach_reference is None
    assert bd["armor_approach"] == 0.0


def test_approach_potential_pays_toward_west_statue_and_telescopes() -> None:
    from re1_rl.armor_room_puzzle import (
        ARMOR_APPROACH_BUDGET,
        ARMOR_APPROACH_STEP,
        armor_approach_progress_reward,
    )

    q = _far_queue()
    progress = ProgressTracker()
    start = _pl79_spawn_state()
    toward = _pl79_spawn_state(x=start["x"] - 350, step=2)
    bd = _far_leg_reward(start, toward, q, progress)
    assert progress.armor_far_approach_reference is not None
    assert 0.0 < bd["armor_approach"] <= ARMOR_APPROACH_STEP
    away = _pl79_spawn_state(x=start["x"] + 350, step=2)
    assert armor_approach_progress_reward(
        start, away, q, progress.armor_far_approach_reference
    ) < 0.0
    # Walk from spawn to the west approach dock in small hops: total <= 0.5.
    total = 0.0
    prev = start
    for k in range(1, 41):
        frac = k / 40.0
        cur = _pl79_spawn_state(
            x=int(start["x"] + (ARMOR_WEST_APPROACH_XZ[0] - start["x"]) * frac),
            z=int(start["z"] + (ARMOR_WEST_APPROACH_XZ[1] - start["z"]) * frac),
            step=k + 1,
        )
        total += armor_approach_progress_reward(
            prev, cur, q, progress.armor_far_approach_reference
        )
        prev = cur
    assert 0.3 < total <= ARMOR_APPROACH_BUDGET


def test_approach_progress_does_not_reset_idle_clock() -> None:
    q = _far_queue()
    progress = ProgressTracker()
    progress.note_stagnation_step(made_progress=False, step_frames=1000)
    start = _pl79_spawn_state()
    toward = _pl79_spawn_state(x=start["x"] - 350, step=2)
    bd = _far_leg_reward(start, toward, q, progress)
    assert bd["armor_approach"] > 0.0
    assert progress.stagnation_frames > 1000


def test_approach_potential_silent_while_pushing_and_off_far_step() -> None:
    from re1_rl.armor_room_puzzle import armor_approach_progress_reward

    q = _far_queue()
    start = _pl79_spawn_state()
    toward = _pl79_spawn_state(x=start["x"] - 350, step=2)
    ref = 5000.0
    assert armor_approach_progress_reward(start, toward, q, None) == 0.0
    pushing = dict(toward, game_state=PUSH_GAME_STATE)
    assert armor_approach_progress_reward(start, pushing, q, ref) == 0.0
    east_q = PlannerLoyalQueue()
    idx = next(
        i for i, s in enumerate(east_q._steps) if s.get("beat_id") == "armor_vent_door"
    )
    east_q.seek(idx)
    assert armor_approach_progress_reward(start, toward, east_q, ref) == 0.0


def test_gas_damage_in_205_is_terminal_and_zeros_positives() -> None:
    from re1_rl.armor_room_puzzle import ARMOR_GAS_DAMAGE_PENALTY

    q = _far_queue()
    progress = ProgressTracker()
    prev = _pl79_spawn_state(hp=96)
    gassed = _pl79_spawn_state(hp=90, x=14345 - 350, step=2)
    bd = _far_leg_reward(prev, gassed, q, progress)
    assert bd["armor_gas"] == pytest.approx(-ARMOR_GAS_DAMAGE_PENALTY)
    assert progress.armor_gas_breached is True
    assert bd["armor_approach"] == 0.0
    assert bd["hp"] < 0.0
    # Second breach never re-pays.
    bd2 = _far_leg_reward(gassed, _pl79_spawn_state(hp=84, step=3), q, progress)
    assert bd2.get("armor_gas", 0.0) == 0.0


def test_gas_detector_ignores_other_rooms_heals_and_death() -> None:
    from re1_rl.armor_room_puzzle import armor_gas_damage_detected

    prev = _pl79_spawn_state(hp=96)
    assert armor_gas_damage_detected(prev, _pl79_spawn_state(hp=90)) is True
    assert armor_gas_damage_detected(prev, _pl79_spawn_state(hp=96)) is False
    assert armor_gas_damage_detected(prev, _pl79_spawn_state(hp=100)) is False
    assert (
        armor_gas_damage_detected(prev, _pl79_spawn_state(hp=0, dead=True))
        is False
    )
    hall_prev = dict(prev, room_id="204")
    assert armor_gas_damage_detected(hall_prev, _pl79_spawn_state(hp=90)) is False
    assert (
        armor_gas_damage_detected(prev, _pl79_spawn_state(hp=90, room_id="204"))
        is False
    )
