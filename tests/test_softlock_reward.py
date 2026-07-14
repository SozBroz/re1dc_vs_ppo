"""Stagnation: grace window, per-step tax, bulk softlock at timeout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    ITEM_PICKUP_BONUS,
    NEW_ROOM_BONUS,
    REFERENCE_STEP_FRAMES,
    REWARD_SCALE,
    SOFTLOCK_FRAME_THRESHOLD,
    SOFTLOCK_TIMEOUT_PENALTY,
    STAGNANT_GRACE_FRAMES,
    STAGNANT_STEP_EXTRA_PENALTY,
    stagnation_episode_timeout,
    compute_reward,
    softlock_reward_from_breakdown,
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


def test_softlock_is_minus_one_checkpoint():
    assert SOFTLOCK_TIMEOUT_PENALTY == pytest.approx(-1.0)
    assert softlock_reward_from_breakdown(
        {"softlock": SOFTLOCK_TIMEOUT_PENALTY}
    ) == pytest.approx(SOFTLOCK_TIMEOUT_PENALTY * REWARD_SCALE)


def test_grace_period_no_stagnant_step_tax():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    steps_to_grace = STAGNANT_GRACE_FRAMES // REFERENCE_STEP_FRAMES
    for i in range(1, steps_to_grace + 1):
        cur = make_state(room="105", step=i)
        _, bd = _step(progress, prev, cur)
        assert bd["stagnant_step"] == 0.0
        assert bd["softlock"] == 0.0
        prev = cur
    assert progress.stagnation_frames == STAGNANT_GRACE_FRAMES


def test_stagnant_step_tax_after_grace():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    steps_past_grace = STAGNANT_GRACE_FRAMES // REFERENCE_STEP_FRAMES + 1
    bd = None
    for i in range(1, steps_past_grace + 1):
        cur = make_state(room="105", step=i)
        _, bd = _step(progress, prev, cur)
        prev = cur
    assert bd is not None
    assert bd["stagnant_step"] == STAGNANT_STEP_EXTRA_PENALTY
    assert bd["softlock"] == 0.0


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
    assert bd["stagnant_step"] == 0.0
    assert progress.stagnation_frames == 0


def test_first_visits_reset_idle_timer():
    progress = ProgressTracker()
    progress.first_visit("105")
    path = ["105", "106", "104", "203"]
    prev = make_state(room="105", step=0)
    for i, room in enumerate(path[1:], start=1):
        cur = make_state(room=room, step=i)
        _, bd = _step(progress, prev, cur, step_frames=4)
        assert bd["stagnant_step"] == 0.0
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


def test_full_stall_episode_includes_bulk_softlock():
    """Full stall to SOFTLOCK_FRAME_THRESHOLD: step + stagnant tax + terminal softlock lump."""
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    total = 0.0
    softlock_sum = 0.0
    threshold = SOFTLOCK_FRAME_THRESHOLD
    step_frames = REFERENCE_STEP_FRAMES
    steps = threshold // step_frames
    for i in range(1, steps + 1):
        cur = make_state(room="105", step=i)
        rew, bd = _step(progress, prev, cur, step_frames=step_frames)
        total += rew
        softlock_sum += bd["softlock"] * REWARD_SCALE
        prev = cur
    assert stagnation_episode_timeout(progress, threshold=threshold)
    assert softlock_sum == pytest.approx(SOFTLOCK_TIMEOUT_PENALTY * REWARD_SCALE)
    # Dense step+stagnant tax ~-4.2 plus lump -1.0 over the 12-minute window
    assert -6.5 < total < -4.0
