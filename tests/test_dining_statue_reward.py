"""Reward + obs for dining 2F statue push."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.dining_statue_puzzle import (
    DINING_STATUE_REWARD,
    dining_statue_knocked_from_state,
)
from re1_rl.obs_encoder import GOAL_DIM, GOAL_FIELDS, ObsEncoder
from re1_rl.progress import ProgressTracker
from re1_rl.reward import DINING_STATUE_BONUS, SOFTLOCK_EXTENSION_FRAMES, compute_reward
from re1_rl.room_graph import RoomGraph
from tests.test_scaffolding import DOORS, ROOMS, make_planner, make_state


def _reward(progress: ProgressTracker, prev: dict, state: dict):
    return compute_reward(
        prev,
        state,
        make_planner(),
        progress=progress,
        return_breakdown=True,
    )


def test_dining_statue_rising_edge_pays_and_extends() -> None:
    progress = ProgressTracker()
    progress._stagnation_frames = 500
    prev = make_state(room="202", dining_statue_flag=0)
    knocked = make_state(room="202", dining_statue_flag=0x10)

    total, bd = _reward(progress, prev, knocked)
    assert bd["dining_statue"] == pytest.approx(DINING_STATUE_BONUS)
    assert bd["dining_statue"] == pytest.approx(DINING_STATUE_REWARD)
    assert progress.dining_statue_rewarded is True
    assert progress.stagnation_frames == 0
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES

    _total2, bd2 = _reward(progress, knocked, knocked)
    assert bd2["dining_statue"] == 0.0


def test_dining_statue_no_pay_when_already_knocked_at_episode_start() -> None:
    progress = ProgressTracker()
    prev = make_state(room="202", dining_statue_flag=0x10)
    cur = make_state(room="202", dining_statue_flag=0x10, step=2)
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
