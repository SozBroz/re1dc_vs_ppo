"""Stagnation: grace window, per-step tax, episode timeout (no lump softlock)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    ITEM_PICKUP_BONUS,
    NEW_ROOM_BONUS,
    REFERENCE_STEP_FRAMES,
    SOFTLOCK_FRAME_THRESHOLD,
    SOFTLOCK_TIMEOUT_PENALTY,
    STAGNANT_GRACE_FRAMES,
    STAGNANT_STEP_EXTRA_PENALTY,
    STEP_PENALTY,
    stagnation_episode_timeout,
    compute_reward,
)
from tests.test_scaffolding import make_planner, make_state


def _step(
    progress,
    prev,
    cur,
    *,
    step_frames=REFERENCE_STEP_FRAMES,
):
    cur = dict(cur)
    cur.setdefault("step_emulated_frames", step_frames)
    cur.setdefault("reference_step_frames", REFERENCE_STEP_FRAMES)
    return compute_reward(
        prev,
        cur,
        make_planner(),
        progress=progress,
        return_breakdown=True,
    )


def test_no_lump_softlock_penalty():
    assert SOFTLOCK_TIMEOUT_PENALTY == 0.0


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


def test_stagnation_timeout_at_threshold():
    progress = ProgressTracker()
    progress.first_visit("105")
    threshold = 40
    step_frames = 4
    prev = make_state(room="105", step=0)
    for i in range(1, 20):
        cur = make_state(room="105", step=i)
        _step(progress, prev, cur, step_frames=step_frames)
        prev = cur
    assert stagnation_episode_timeout(progress, threshold=threshold)
    assert progress.stagnation_frames >= threshold


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
        _, bd = _step(progress, prev, cur, step_frames=step_frames)
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


def test_full_stall_episode_return_order_of_magnitude():
    """~2400 stagnant env steps: step + stagnant tax, no lump."""
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    total = 0.0
    threshold = SOFTLOCK_FRAME_THRESHOLD
    step_frames = REFERENCE_STEP_FRAMES
    steps = threshold // step_frames
    for i in range(1, steps + 1):
        cur = make_state(room="105", step=i)
        rew, _ = _step(progress, prev, cur, step_frames=step_frames)
        total += rew
        prev = cur
    assert stagnation_episode_timeout(progress, threshold=threshold)
    # Grace ~600 steps @ -0.0002, then ~1800 @ -0.0004 ≈ -0.84
    assert -1.5 < total < -0.3
