"""Stagnation: bulk softlock at timeout; spread over n_steps on learner."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.distributed.learner_train import compute_dual_gamma_mc_returns
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    CONTEMPT_BUDGET_SCALED,
    DEATH_PENALTY,
    DEATH_PENALTY_SCALED,
    ITEM_PICKUP_BONUS,
    NEW_ROOM_BONUS,
    REFERENCE_STEP_FRAMES,
    REWARD_SCALE,
    SOFTLOCK_FRAME_THRESHOLD,
    SOFTLOCK_GAMMA,
    SOFTLOCK_TIMEOUT_PENALTY,
    stagnation_episode_timeout,
    compute_reward,
    softlock_reward_from_breakdown,
    spread_softlock_contempt_over_horizon,
)
from tests.test_scaffolding import make_planner, make_state


def _step(
    progress,
    prev,
    cur,
    *,
    step_frames=REFERENCE_STEP_FRAMES,
    softlock_threshold=SOFTLOCK_FRAME_THRESHOLD,
):
    cur = dict(cur)
    cur.setdefault("step_emulated_frames", step_frames)
    cur.setdefault("reference_step_frames", REFERENCE_STEP_FRAMES)
    return compute_reward(
        prev,
        cur,
        make_planner(),
        progress=progress,
        softlock_threshold=softlock_threshold,
        return_breakdown=True,
    )


def test_softlock_matches_death_budget():
    assert CONTEMPT_BUDGET_SCALED == pytest.approx(DEATH_PENALTY_SCALED)
    assert SOFTLOCK_TIMEOUT_PENALTY == pytest.approx(-DEATH_PENALTY_SCALED)
    assert softlock_reward_from_breakdown(
        {"softlock": SOFTLOCK_TIMEOUT_PENALTY}
    ) == pytest.approx(SOFTLOCK_TIMEOUT_PENALTY * REWARD_SCALE)


def test_no_per_step_stagnant_tax():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    for i in range(1, 200):
        cur = make_state(room="105", step=i)
        _, bd = _step(progress, prev, cur)
        assert bd["softlock"] == 0.0
        prev = cur


def test_stagnation_timeout_applies_bulk_softlock():
    progress = ProgressTracker()
    progress.first_visit("105")
    threshold = 40
    step_frames = 4
    prev = make_state(room="105", step=0)
    bd = None
    for i in range(1, 11):
        cur = make_state(room="105", step=i)
        _, bd = _step(
            progress, prev, cur, step_frames=step_frames, softlock_threshold=threshold
        )
        prev = cur
    assert stagnation_episode_timeout(progress, threshold=threshold)
    assert bd is not None
    assert bd["softlock"] == pytest.approx(SOFTLOCK_TIMEOUT_PENALTY)


def test_contempt_equals_death_budget_not_greater():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    softlock_sum = 0.0
    threshold = SOFTLOCK_FRAME_THRESHOLD
    step_frames = REFERENCE_STEP_FRAMES
    steps = threshold // step_frames
    for i in range(1, steps + 1):
        cur = make_state(room="105", step=i)
        _, bd = _step(progress, prev, cur, step_frames=step_frames)
        softlock_sum += bd["softlock"]
        prev = cur
    assert stagnation_episode_timeout(progress, threshold=threshold)
    contempt = -softlock_sum
    assert contempt == pytest.approx(CONTEMPT_BUDGET_SCALED)
    assert contempt <= DEATH_PENALTY_SCALED + 1e-9


def test_death_penalty_not_weaker_than_softlock_contempt():
    assert abs(DEATH_PENALTY) == pytest.approx(CONTEMPT_BUDGET_SCALED)


def test_spread_softlock_uniform_over_horizon():
    rewards = np.zeros((4, 1), dtype=np.float32)
    rewards[3, 0] = -1.0
    softlock = np.zeros((4, 1), dtype=np.float32)
    softlock[3, 0] = -1.0
    dones = np.zeros((4, 1), dtype=np.bool_)
    dones[3, 0] = True
    spread_softlock_contempt_over_horizon(
        rewards, softlock, dones, horizon=4
    )
    assert softlock[:, 0] == pytest.approx(-0.25)
    assert rewards[:, 0] == pytest.approx(-0.25)
    # main channel unchanged
    assert (rewards - softlock)[:, 0] == pytest.approx(0.0)


def test_spread_softlock_mc_sums_to_lump():
    n = 4
    lump = -CONTEMPT_BUDGET_SCALED
    rewards = np.zeros((n, 1), dtype=np.float32)
    rewards[-1, 0] = lump
    softlock = np.zeros((n, 1), dtype=np.float32)
    softlock[-1, 0] = lump
    dones = np.zeros((n, 1), dtype=np.bool_)
    dones[-1, 0] = True
    spread_softlock_contempt_over_horizon(rewards, softlock, dones, horizon=n)
    values = np.zeros_like(rewards)
    last_values = np.array([0.0], dtype=np.float32)
    returns, _ = compute_dual_gamma_mc_returns(
        rewards,
        softlock,
        dones,
        values,
        last_values,
        gamma_main=0.99,
        gamma_softlock=SOFTLOCK_GAMMA,
    )
    assert returns[0, 0] == pytest.approx(lump, rel=1e-5)


def test_room_loop_without_new_visits_accumulates_stagnation():
    progress = ProgressTracker()
    for r in ("105", "106", "104"):
        progress.first_visit(r)
    path = ["105", "106", "104", "106", "105"]
    step_frames = 4
    threshold = (len(path) - 1) * step_frames
    prev = make_state(room="105", step=0)
    for i, room in enumerate(path[1:], start=1):
        cur = make_state(room=room, step=i)
        _, bd = _step(
            progress, prev, cur, step_frames=step_frames, softlock_threshold=10_000
        )
        assert bd["new_room"] == 0.0
        assert bd["softlock"] == 0.0
        prev = cur
    assert stagnation_episode_timeout(progress, threshold=threshold)


def test_junk_item_pickup_does_not_reset_idle_timer():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    cur = make_state(room="105", step=1, new_items=["green_herb"])
    _, bd = _step(progress, prev, cur, step_frames=4)
    assert bd["item"] == ITEM_PICKUP_BONUS
    assert bd["key_item"] == 0.0
    assert progress.stagnation_frames == 4


def test_key_item_pickup_resets_idle_timer():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    for i in range(1, 4):
        cur = make_state(room="105", step=i)
        _step(progress, prev, cur, step_frames=4)
        prev = cur
    assert progress.stagnation_frames == 12
    cur = make_state(room="105", step=4, new_items=["emblem"])
    _, bd = _step(progress, prev, cur, step_frames=4)
    assert bd["key_item"] > 0.0
    assert progress.stagnation_frames == 0


def test_first_visits_reset_idle_timer():
    progress = ProgressTracker()
    progress.first_visit("105")
    path = ["105", "106", "104", "203"]
    prev = make_state(room="105", step=0)
    for i, room in enumerate(path[1:], start=1):
        cur = make_state(room=room, step=i)
        _, bd = _step(progress, prev, cur, step_frames=4)
        assert bd["new_room"] == NEW_ROOM_BONUS
        prev = cur
    assert progress.stagnation_frames == 0


def test_long_step_advances_stagnation_proportionally():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    short = make_state(room="105", step=1)
    _step(progress, prev, short, step_frames=4)
    assert progress.stagnation_frames == 4

    progress._stagnation_frames = 0
    long_step = make_state(room="105", step=2)
    _step(progress, short, long_step, step_frames=120)
    assert progress.stagnation_frames == 120
