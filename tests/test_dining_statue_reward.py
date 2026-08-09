"""Reward + obs for dining 2F statue push."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.dining_statue_puzzle import (
    DINING_STATUE_DROP_XZ,
    DINING_STATUE_FINAL_PUSH_XZ,
    DINING_STATUE_PROGRESS_BUDGET,
    DINING_STATUE_PROGRESS_STEP,
    DINING_STATUE_REWARD,
    dining_statue_knocked_from_state,
    dining_statue_nav_target,
    dining_statue_progress_reward,
    encode_dining_statue_compass,
)
from re1_rl.obs_encoder import GOAL_DIM, GOAL_FIELDS, ObsEncoder
from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import DINING_STATUE_BONUS, SOFTLOCK_EXTENSION_FRAMES, compute_reward
from re1_rl.room_graph import RoomGraph
from tests.test_scaffolding import DOORS, ROOMS, make_planner, make_state

YAWN_ROUTE = Path(__file__).resolve().parents[1] / "data" / "yawn_checkpoint_route.json"


def _reward(progress: ProgressTracker, prev: dict, state: dict, *, planner=None, rails_mode=False):
    return compute_reward(
        prev,
        state,
        planner if planner is not None else make_planner(),
        progress=progress,
        rails_mode=rails_mode,
        return_breakdown=True,
    )


def _statue_planner() -> WaypointPlanner:
    return WaypointPlanner(YAWN_ROUTE, route_steps=[55])


def _statue_state(**kw):
    base = dict(
        room="202",
        dining_statue_knocked=False,
        dining_statue_flag=0,
        in_control=True,
    )
    base.update(kw)
    return make_state(**base)

def test_dining_statue_rising_edge_pays_and_extends() -> None:
    progress = ProgressTracker()
    progress._stagnation_frames = 500
    prev = make_state(room="202", dining_statue_flag=0)
    knocked = make_state(room="202", dining_statue_flag=0x10)

    total, bd = _reward(progress, prev, knocked)
    assert bd["dining_statue"] == pytest.approx(DINING_STATUE_BONUS)
    assert bd["dining_statue"] == pytest.approx(DINING_STATUE_REWARD)
    assert progress.dining_statue_rewarded is True
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES

    _, bd2 = _reward(progress, knocked, knocked)
    assert bd2["dining_statue"] == 0.0


def test_dining_statue_no_pay_when_already_knocked_at_episode_start() -> None:
    progress = ProgressTracker()
    prev = make_state(room="202", dining_statue_flag=0x10)
    cur = make_state(room="202", dining_statue_flag=0x10, step=2)
    _total, bd = _reward(progress, prev, cur)
    assert bd["dining_statue"] == 0.0
    assert progress.dining_statue_rewarded is False


def test_dining_statue_no_pay_outside_room_202() -> None:
    progress = ProgressTracker()
    prev = make_state(room="105", dining_statue_flag=0, in_control=True)
    cur = make_state(room="105", dining_statue_flag=0x10, in_control=True)
    _total, bd = _reward(progress, prev, cur)
    assert bd["dining_statue"] == 0.0
    assert progress.dining_statue_rewarded is False


def test_dining_statue_no_pay_during_skip() -> None:
    progress = ProgressTracker()
    prev = make_state(room="202", dining_statue_flag=0, in_control=True)
    cur = make_state(room="202", dining_statue_flag=0x10, in_control=False)
    _total, bd = _reward(progress, prev, cur)
    assert bd["dining_statue"] == 0.0
    assert progress.dining_statue_rewarded is False


def test_dining_statue_knocked_from_state() -> None:
    assert not dining_statue_knocked_from_state(make_state(dining_statue_flag=0))
    assert dining_statue_knocked_from_state(make_state(dining_statue_flag=0x10))


def test_goal_vector_exposes_dining_statue_knocked() -> None:
    enc = ObsEncoder(ROOMS, RoomGraph(DOORS))
    planner = make_planner()
    idx = next(i for i, (name, _) in enumerate(GOAL_FIELDS) if name == "dining_statue_knocked")
    goal = enc.encode_goal(make_state(dining_statue_knocked=True), planner)
    assert goal.shape == (GOAL_DIM,)
    assert goal[idx] == pytest.approx(1.0)


def test_nav_target_is_drop_until_statue_arrives() -> None:
    far = make_state(
        room="202",
        dining_statue_x=23000,
        dining_statue_z=3500,
        dining_statue_knocked=False,
    )
    assert dining_statue_nav_target(far) == (
        float(DINING_STATUE_DROP_XZ[0]),
        float(DINING_STATUE_DROP_XZ[1]),
    )
    near = make_state(
        room="202",
        dining_statue_x=DINING_STATUE_DROP_XZ[0],
        dining_statue_z=DINING_STATUE_DROP_XZ[1],
        dining_statue_knocked=False,
    )
    assert dining_statue_nav_target(near) == (
        float(DINING_STATUE_FINAL_PUSH_XZ[0]),
        float(DINING_STATUE_FINAL_PUSH_XZ[1]),
    )


def test_statue_202_overrides_door_compass_toward_drop() -> None:
    enc = ObsEncoder(ROOMS, RoomGraph(DOORS))
    planner = _statue_planner()
    assert planner.current_objective()["checkpoint_id"] == "statue_202"
    # East of the drop line, facing west-ish (2048) — drop should be ahead.
    state = make_state(
        room="202",
        x=20000,
        z=3452,
        facing=2048,
        dining_statue_x=20000,
        dining_statue_z=3452,
        dining_statue_knocked=False,
    )
    compass = encode_dining_statue_compass(state, planner)
    assert compass is not None
    goal = enc.encode_goal(state, planner)
    assert goal[5:10] == pytest.approx(compass)
    assert goal[21] == pytest.approx(1.0)
    # Drop X is west of player → negative dx.
    assert goal[5] < 0.0
    assert goal[7] > 0.0


def test_statue_202_compass_off_when_knocked() -> None:
    planner = _statue_planner()
    state = make_state(
        room="202",
        dining_statue_knocked=True,
        dining_statue_x=DINING_STATUE_DROP_XZ[0],
        dining_statue_z=DINING_STATUE_DROP_XZ[1],
    )
    assert encode_dining_statue_compass(state, planner) is None


def test_statue_progress_pays_half_when_closing() -> None:
    planner = _statue_planner()
    # One step that closes enough for the full +0.5 clip.
    far = _statue_state(dining_statue_x=23000, dining_statue_z=3452)
    closer = _statue_state(dining_statue_x=19000, dining_statue_z=3452)
    pay = dining_statue_progress_reward(far, closer, planner)
    assert pay == pytest.approx(DINING_STATUE_PROGRESS_STEP)


def test_statue_progress_penalizes_retreat() -> None:
    planner = _statue_planner()
    near = _statue_state(dining_statue_x=18000, dining_statue_z=3452)
    far = _statue_state(dining_statue_x=22000, dining_statue_z=3452)
    pay = dining_statue_progress_reward(near, far, planner)
    assert pay == pytest.approx(-DINING_STATUE_PROGRESS_STEP)


def test_statue_progress_full_shove_near_budget() -> None:
    planner = _statue_planner()
    # Walk the statue from a long balcony start to the drop in small steps.
    xs = list(range(23000, DINING_STATUE_DROP_XZ[0] - 1, -200))
    xs.append(DINING_STATUE_DROP_XZ[0])
    total = 0.0
    for a, b in zip(xs, xs[1:]):
        prev = _statue_state(dining_statue_x=a, dining_statue_z=3452)
        cur = _statue_state(dining_statue_x=b, dining_statue_z=3452)
        total += dining_statue_progress_reward(prev, cur, planner)
    assert 6.0 <= total <= DINING_STATUE_PROGRESS_BUDGET + 0.01


def test_statue_progress_in_compute_reward_breakdown() -> None:
    progress = ProgressTracker()
    planner = _statue_planner()
    prev = _statue_state(dining_statue_x=23000, dining_statue_z=3452)
    cur = _statue_state(dining_statue_x=19000, dining_statue_z=3452, step=2)
    _total, bd = _reward(progress, prev, cur, planner=planner, rails_mode=True)
    assert bd["dining_statue_progress"] == pytest.approx(DINING_STATUE_PROGRESS_STEP)
    assert bd["dining_statue"] == 0.0


def test_statue_progress_off_when_not_statue_checkpoint() -> None:
    planner = make_planner()  # not statue_202
    far = _statue_state(dining_statue_x=23000, dining_statue_z=3452)
    closer = _statue_state(dining_statue_x=19000, dining_statue_z=3452)
    assert dining_statue_progress_reward(far, closer, planner) == 0.0
