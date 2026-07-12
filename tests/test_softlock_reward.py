"""Softlock: dwell stagnation + door thrash (A↔B) without progress."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    NEW_ROOM_BONUS,
    SOFTLOCK_STEP_THRESHOLD,
    SOFTLOCK_THRASH_TRANSITION_THRESHOLD,
    SOFTLOCK_TIMEOUT_PENALTY,
    compute_reward,
)
from tests.test_scaffolding import make_planner, make_state


def _step(progress, prev, cur, *, softlock_threshold=SOFTLOCK_STEP_THRESHOLD):
    return compute_reward(
        prev,
        cur,
        make_planner(),
        progress=progress,
        softlock_threshold=softlock_threshold,
        return_breakdown=True,
    )


def test_dwell_past_threshold_fires_softlock():
    """Sitting in one room with no progress hits softlock at the threshold."""
    progress = ProgressTracker()
    progress.first_visit("105")
    threshold = 10
    prev = make_state(room="105", step=0)
    softlock_hits = 0
    for i in range(1, threshold + 1):
        cur = make_state(room="105", step=i)
        _, bd = _step(progress, prev, cur, softlock_threshold=threshold)
        if bd["softlock"] != 0.0:
            softlock_hits += 1
            assert bd["softlock"] == SOFTLOCK_TIMEOUT_PENALTY
        prev = cur
    assert softlock_hits == 1


def test_door_thrash_fires_softlock():
    """Oscillating A↔B past thrash threshold fires softlock; sitting does not dodge."""
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.first_visit("106")
    n = SOFTLOCK_THRASH_TRANSITION_THRESHOLD
    rooms = ["105", "106"]
    prev = make_state(room="105", step=0)
    thrash_hits = 0
    for i in range(1, n + 1):
        room = rooms[i % 2]
        cur = make_state(room=room, step=i)
        _, bd = _step(progress, prev, cur, softlock_threshold=10_000)
        if bd["softlock"] != 0.0:
            thrash_hits += 1
            assert bd["softlock"] == SOFTLOCK_TIMEOUT_PENALTY
        assert bd["new_room"] == 0.0
        prev = cur
    assert thrash_hits == 1
    assert progress._thrash_transitions == n


def test_first_visits_do_not_false_thrash():
    """Legitimate path through new rooms pays new_room and never thrash-penalizes."""
    progress = ProgressTracker()
    progress.first_visit("105")
    path = ["105", "106", "104", "203", "204"]
    prev = make_state(room="105", step=0)
    for i, room in enumerate(path[1:], start=1):
        cur = make_state(room=room, step=i)
        _, bd = _step(progress, prev, cur, softlock_threshold=10_000)
        assert bd["softlock"] == 0.0
        assert bd["new_room"] == NEW_ROOM_BONUS
        prev = cur
    assert progress._thrash_transitions == 0
    assert progress._stagnation_steps == 0


def test_thrash_resets_on_third_room():
    """Leaving the A↔B pair for a third room clears the thrash streak."""
    progress = ProgressTracker()
    for r in ("105", "106", "104"):
        progress.first_visit(r)
    # Build thrash toward (but not over) threshold, then break to C.
    n = SOFTLOCK_THRASH_TRANSITION_THRESHOLD - 1
    rooms = ["105", "106"]
    prev = make_state(room="105", step=0)
    for i in range(1, n + 1):
        cur = make_state(room=rooms[i % 2], step=i)
        _, bd = _step(progress, prev, cur, softlock_threshold=10_000)
        assert bd["softlock"] == 0.0
        prev = cur
    assert progress._thrash_transitions == n

    cur = make_state(room="104", step=n + 1)
    _, bd = _step(progress, prev, cur, softlock_threshold=10_000)
    assert bd["softlock"] == 0.0
    assert progress._thrash_edge == frozenset({"106", "104"}) or progress._thrash_edge == frozenset(
        {prev["room_id"], "104"}
    )
    assert progress._thrash_transitions == 1


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
