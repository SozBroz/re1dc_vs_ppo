"""Stagnation: grace then ramping softlock in scalar reward."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    CONTEMPT_BUDGET_SCALED,
    CONTEMPT_GRACE_FRAMES,
    DEATH_PENALTY,
    DEATH_PENALTY_SCALED,
    ITEM_PICKUP_BONUS,
    NEW_ROOM_BONUS,
    REFERENCE_STEP_FRAMES,
    SOFTLOCK_FRAME_THRESHOLD,
    SOFTLOCK_TIMEOUT_PENALTY,
    SURVIVAL_BUDGET_SCALED,
    contempt_penalty_delta,
    contempt_spent_at,
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
    assert CONTEMPT_BUDGET_SCALED < SURVIVAL_BUDGET_SCALED


def test_grace_has_no_softlock_tax():
    """Strictly under the 3 min cap: no softlock tax (cap frame is the truncate)."""
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    step_frames = REFERENCE_STEP_FRAMES
    steps = (CONTEMPT_GRACE_FRAMES // step_frames) - 1
    softlock_sum = 0.0
    for i in range(1, steps + 1):
        cur = make_state(room="105", step=i)
        _, bd = _step(progress, prev, cur, step_frames=step_frames)
        softlock_sum += bd["softlock"]
        prev = cur
    assert progress.stagnation_frames == steps * step_frames
    assert progress.stagnation_frames < CONTEMPT_GRACE_FRAMES
    assert softlock_sum == pytest.approx(0.0)


def test_ramp_integral_equals_death_budget_not_survival():
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
    assert contempt == pytest.approx(CONTEMPT_BUDGET_SCALED, rel=1e-5)
    assert contempt <= DEATH_PENALTY_SCALED + 1e-9
    assert contempt < SURVIVAL_BUDGET_SCALED


def test_contempt_spent_at_grace_equals_threshold():
    """3 min truncate == grace: free before cap, full budget on the timeout frame."""
    grace = CONTEMPT_GRACE_FRAMES
    threshold = SOFTLOCK_FRAME_THRESHOLD
    assert grace == threshold
    assert contempt_spent_at(grace - 1) == pytest.approx(0.0)
    assert contempt_spent_at(threshold) == pytest.approx(CONTEMPT_BUDGET_SCALED)


def test_contempt_spent_quadratic_mid_ramp_when_ramp_exists():
    grace = 100
    threshold = 500
    ramp = threshold - grace
    mid = grace + ramp // 2
    spent = contempt_spent_at(mid, grace=grace, threshold=threshold)
    assert spent == pytest.approx(CONTEMPT_BUDGET_SCALED * 0.25, rel=1e-6)
    assert contempt_spent_at(grace, grace=grace, threshold=threshold) == pytest.approx(0.0)
    assert contempt_spent_at(
        threshold, grace=grace, threshold=threshold
    ) == pytest.approx(CONTEMPT_BUDGET_SCALED)


def test_short_threshold_falls_back_to_bulk_at_timeout():
    """When threshold ≤ grace, full budget hits on the timeout step."""
    progress = ProgressTracker()
    progress.first_visit("105")
    threshold = 40
    step_frames = 4
    prev = make_state(room="105", step=0)
    bd = None
    softlock_sum = 0.0
    for i in range(1, 11):
        cur = make_state(room="105", step=i)
        _, bd = _step(
            progress, prev, cur, step_frames=step_frames, softlock_threshold=threshold
        )
        softlock_sum += bd["softlock"]
        prev = cur
    assert stagnation_episode_timeout(progress, threshold=threshold)
    assert bd is not None
    assert softlock_sum == pytest.approx(SOFTLOCK_TIMEOUT_PENALTY)
    assert bd["softlock"] == pytest.approx(SOFTLOCK_TIMEOUT_PENALTY)


def test_death_penalty_not_weaker_than_softlock_contempt():
    assert abs(DEATH_PENALTY) == pytest.approx(CONTEMPT_BUDGET_SCALED)


def test_progress_resets_ramp():
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.rewarded_cutscenes.add("104:0:s0")
    prev = make_state(room="105", step=0)
    # Sit past grace into the ramp.
    progress._stagnation_frames = CONTEMPT_GRACE_FRAMES + 600
    assert contempt_spent_at(progress.stagnation_frames) > 0.0
    cur = make_state(room="106", step=1)
    _, bd = _step(progress, prev, cur, step_frames=REFERENCE_STEP_FRAMES)
    assert bd["new_room"] == NEW_ROOM_BONUS
    assert bd["softlock"] == 0.0
    assert progress.stagnation_frames == 0
    # Fresh grace after reset.
    prev = cur
    cur = make_state(room="106", step=2)
    _, bd = _step(progress, prev, cur, step_frames=REFERENCE_STEP_FRAMES)
    assert bd["softlock"] == 0.0
    assert progress.stagnation_frames == REFERENCE_STEP_FRAMES


def test_room_loop_without_new_visits_accumulates_stagnation():
    progress = ProgressTracker()
    for r in ("105", "106", "104"):
        progress.first_visit(r)
    # Legal 106 re-entry under the sole Kenneth gate (illegal entry ends episode).
    progress.rewarded_cutscenes.add("104:0:s0")
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
        assert bd["death"] == 0.0
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
    assert bd["new_weapon"] == 0.0
    assert progress.stagnation_frames == 4


def test_weapon_pickup_resets_idle_timer():
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    for i in range(1, 4):
        cur = make_state(room="105", step=i)
        _step(progress, prev, cur, step_frames=4)
        prev = cur
    assert progress.stagnation_frames == 12
    cur = make_state(room="115", step=4, new_items=["colt_python"])
    _, bd = _step(progress, prev, cur, step_frames=4)
    from re1_rl.reward import NEW_WEAPON_PICKUP_BONUS

    assert bd["new_weapon"] == NEW_WEAPON_PICKUP_BONUS
    assert bd["item"] == 0.0
    assert bd["key_item"] == 0.0
    assert progress.stagnation_frames == 0


def test_shotgun_wall_loop_is_zero_sum_and_pickup_extends_episode():
    from re1_rl.reward import (
        NEW_WEAPON_PICKUP_BONUS,
        SHOTGUN_RETURN_PENALTY,
    )

    progress = ProgressTracker()
    progress.first_visit("115")
    progress._stagnation_frames = 1234
    empty = make_state(room="115", step=0, inventory=[])

    held = make_state(
        room="115",
        step=1,
        inventory=["shotgun"],
        new_items=["shotgun"],
    )
    pickup_reward, pickup_bd = _step(
        progress, empty, held, step_frames=0
    )
    assert pickup_bd["new_weapon"] == NEW_WEAPON_PICKUP_BONUS
    assert pickup_bd["shotgun_return"] == 0.0
    assert progress.stagnation_frames == 0

    returned = make_state(room="115", step=2, inventory=[], new_items=[])
    return_reward, return_bd = _step(
        progress, held, returned, step_frames=0
    )
    assert return_bd["new_weapon"] == 0.0
    assert return_bd["shotgun_return"] == SHOTGUN_RETURN_PENALTY
    assert pickup_reward + return_reward == 0.0
    # Async post-skip can replay the same held->empty transition next step.
    duplicate_return, duplicate_bd = _step(
        progress, held, returned, step_frames=0
    )
    assert duplicate_bd["shotgun_return"] == 0.0
    assert duplicate_return == 0.0

    # A second take/replace cycle has the same exact zero-sum behavior.
    pickup2, pickup2_bd = _step(
        progress, returned, held, step_frames=0
    )
    return2, return2_bd = _step(
        progress, held, returned, step_frames=0
    )
    assert pickup2_bd["new_weapon"] == NEW_WEAPON_PICKUP_BONUS
    assert return2_bd["shotgun_return"] == SHOTGUN_RETURN_PENALTY
    assert pickup2 + return2 == 0.0


def test_shotgun_removal_outside_rack_rooms_is_not_penalized():
    progress = ProgressTracker()
    progress.first_visit("203")
    held = make_state(room="203", step=0, inventory=["shotgun"])
    empty = make_state(room="203", step=1, inventory=[])
    _, bd = _step(progress, held, empty, step_frames=0)
    assert bd["shotgun_return"] == 0.0


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
    # Kenneth paid so 106 entry is legal under the sole Kenneth gate.
    progress.rewarded_cutscenes.add("104:0:s0")
    path = ["105", "106", "104", "203"]
    prev = make_state(room="105", step=0)
    for i, room in enumerate(path[1:], start=1):
        cur = make_state(room=room, step=i)
        _, bd = _step(progress, prev, cur, step_frames=4)
        assert bd["new_room"] == NEW_ROOM_BONUS
        assert bd["death"] == 0.0
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


def test_contempt_penalty_delta_monotonic():
    """With an explicit ramp window, later slices are steeper."""
    grace = 100
    threshold = 500
    a = grace + 100
    b = grace + 200
    d1 = contempt_penalty_delta(grace, a, grace=grace, threshold=threshold)
    d2 = contempt_penalty_delta(a, b, grace=grace, threshold=threshold)
    assert d1 < 0.0
    assert d2 < 0.0
    assert abs(d2) > abs(d1)
