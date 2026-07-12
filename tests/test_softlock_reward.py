"""Softlock: idle contempt when no new room / cutscene / key item."""

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
    compute_reward,
)
from tests.test_scaffolding import make_planner, make_state


def _step(
    progress,
    prev,
    cur,
    *,
    softlock_threshold=SOFTLOCK_FRAME_THRESHOLD,
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
        softlock_threshold=softlock_threshold,
        return_breakdown=True,
    )


def test_dwell_past_threshold_fires_softlock():
    """Sitting in one room with no progress hits softlock at the frame threshold."""
    progress = ProgressTracker()
    progress.first_visit("105")
    threshold = 10
    step_frames = 4
    prev = make_state(room="105", step=0)
    softlock_hits = 0
    for i in range(1, 20):
        cur = make_state(room="105", step=i)
        _, bd = _step(
            progress,
            prev,
            cur,
            softlock_threshold=threshold,
            step_frames=step_frames,
        )
        if bd["softlock"] != 0.0:
            softlock_hits += 1
            assert bd["softlock"] == SOFTLOCK_TIMEOUT_PENALTY
        if softlock_hits:
            break
        prev = cur
    assert softlock_hits == 1
    assert progress._stagnation_frames >= threshold


def test_room_loop_without_new_visits_still_hits_softlock():
    """A→B→C→B→A with all rooms already seen still accumulates idle frames."""
    progress = ProgressTracker()
    for r in ("105", "106", "104"):
        progress.first_visit(r)
    path = ["105", "106", "104", "106", "105"]
    step_frames = 4
    threshold = (len(path) - 1) * step_frames
    prev = make_state(room="105", step=0)
    softlock_hits = 0
    for i, room in enumerate(path[1:], start=1):
        cur = make_state(room=room, step=i)
        _, bd = _step(
            progress,
            prev,
            cur,
            softlock_threshold=threshold,
            step_frames=step_frames,
        )
        if bd["softlock"] != 0.0:
            softlock_hits += 1
            assert bd["softlock"] == SOFTLOCK_TIMEOUT_PENALTY
        assert bd["new_room"] == 0.0
        prev = cur
    assert softlock_hits == 1
    assert progress._stagnation_frames == threshold


def test_junk_item_pickup_does_not_reset_idle_timer():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    cur = make_state(room="105", step=1, new_items=["green_herb"])
    _, bd = _step(progress, prev, cur, softlock_threshold=20, step_frames=4)
    assert bd["item"] == ITEM_PICKUP_BONUS
    assert bd["key_item"] == 0.0
    assert progress._stagnation_frames == 4


def test_key_item_pickup_resets_idle_timer():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    for i in range(1, 4):
        cur = make_state(room="105", step=i)
        _step(progress, prev, cur, softlock_threshold=20, step_frames=4)
        prev = cur
    assert progress._stagnation_frames == 12
    cur = make_state(room="105", step=4, new_items=["emblem"])
    _, bd = _step(progress, prev, cur, softlock_threshold=20, step_frames=4)
    assert bd["key_item"] > 0.0
    assert bd["softlock"] == 0.0
    assert progress._stagnation_frames == 0


def test_first_visits_reset_idle_timer():
    progress = ProgressTracker()
    progress.first_visit("105")
    path = ["105", "106", "104", "203"]
    prev = make_state(room="105", step=0)
    for i, room in enumerate(path[1:], start=1):
        cur = make_state(room=room, step=i)
        _, bd = _step(progress, prev, cur, softlock_threshold=10_000, step_frames=4)
        assert bd["softlock"] == 0.0
        assert bd["new_room"] == NEW_ROOM_BONUS
        prev = cur
    assert progress._stagnation_frames == 0


def test_new_room_bonus_still_pays_once():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=1)
    cur = make_state(room="106", step=2)
    _, bd0 = _step(progress, prev, cur)
    assert bd0["new_room"] == NEW_ROOM_BONUS
    assert bd0["softlock"] == 0.0
    _, bd1 = _step(progress, cur, prev)
    assert bd1["new_room"] == 0.0


def test_long_step_advances_stagnation_proportionally():
    """Macro steps burn more idle budget than a single hold-tick."""
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    short = make_state(room="105", step=1)
    _step(progress, prev, short, softlock_threshold=10_000, step_frames=4)
    assert progress._stagnation_frames == 4

    progress._stagnation_frames = 0
    long_step = make_state(room="105", step=2)
    _step(progress, short, long_step, softlock_threshold=10_000, step_frames=120)
    assert progress._stagnation_frames == 120
