"""Stagnation: grace then ramping softlock in scalar reward."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    AMMO_PICKUP_BONUS,
    CONTEMPT_BUDGET_SCALED,
    CONTEMPT_GRACE_FRAMES,
    DEATH_PENALTY,
    DEATH_PENALTY_SCALED,
    ITEM_PICKUP_BONUS,
    NEW_ROOM_BONUS,
    REFERENCE_STEP_FRAMES,
    SOFTLOCK_EXTENSION_FRAMES,
    SOFTLOCK_FRAME_THRESHOLD,
    SOFTLOCK_POST_KENNETH_FRAMES,
    SOFTLOCK_PRE_KENNETH_FRAMES,
    SOFTLOCK_TIMEOUT_PENALTY,
    SURVIVAL_BUDGET_SCALED,
    contempt_penalty_delta,
    contempt_spent_at,
    softlock_frame_threshold,
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
    softlock_threshold=None,
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


def test_softlock_budget_is_independent_static():
    assert CONTEMPT_BUDGET_SCALED == pytest.approx(4.0 / 15.0)  # |death|/5
    assert SOFTLOCK_TIMEOUT_PENALTY == pytest.approx(-(4.0 / 15.0))
    assert DEATH_PENALTY_SCALED == pytest.approx(4.0 / 3.0)
    assert CONTEMPT_BUDGET_SCALED < SURVIVAL_BUDGET_SCALED


def test_softlock_start_and_post_kenneth_are_twelve_minutes():
    assert SOFTLOCK_PRE_KENNETH_FRAMES == 12 * 60 * 60
    assert SOFTLOCK_POST_KENNETH_FRAMES == 12 * 60 * 60
    assert SOFTLOCK_FRAME_THRESHOLD == SOFTLOCK_POST_KENNETH_FRAMES
    progress = ProgressTracker()
    assert softlock_frame_threshold(progress) == SOFTLOCK_PRE_KENNETH_FRAMES
    progress.rewarded_cutscenes.add("104:0:s0")
    assert softlock_frame_threshold(progress) == SOFTLOCK_POST_KENNETH_FRAMES


def test_new_room_floors_softlock_cap_at_twelve_minutes():
    """Progress extension floors idle truncate at 12 min (same as start budget)."""
    from re1_rl.reward import SOFTLOCK_EXTENSION_FRAMES

    progress = ProgressTracker()
    progress.first_visit("105")
    assert softlock_frame_threshold(progress) == SOFTLOCK_PRE_KENNETH_FRAMES
    prev = make_state(room="105", step=0)
    cur = make_state(room="104", step=1)
    _, bd = _step(progress, prev, cur)
    assert bd["new_room"] == NEW_ROOM_BONUS
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES
    assert softlock_frame_threshold(progress) == SOFTLOCK_EXTENSION_FRAMES


def test_kenneth_gate_breach_revokes_and_blocks_softlock_extensions():
    from re1_rl.reward import SOFTLOCK_EXTENSION_FRAMES

    progress = ProgressTracker()
    progress.first_visit("105")
    progress.note_softlock_extension(SOFTLOCK_EXTENSION_FRAMES)
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES

    prev = make_state(room="105", step=0)
    hall = make_state(room="106", step=1)
    _, breach = _step(progress, prev, hall)
    assert breach["main_hall_before_kenneth"] == -0.05
    assert progress.kenneth_gate_breached
    assert progress.softlock_cap_frames == 0
    assert softlock_frame_threshold(progress) == SOFTLOCK_PRE_KENNETH_FRAMES

    progress.note_softlock_extension(SOFTLOCK_EXTENSION_FRAMES)
    assert progress.softlock_cap_frames == 0
    tea = make_state(room="104", step=2)
    _, poisoned = _step(progress, hall, tea)
    assert poisoned["new_room"] == 0.0
    assert progress.softlock_cap_frames == 0
    assert softlock_frame_threshold(progress) == SOFTLOCK_PRE_KENNETH_FRAMES


def test_grace_has_no_softlock_tax():
    """Under grace on the post-Kenneth 6m cap: no softlock tax."""
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.rewarded_cutscenes.add("104:0:s0")
    prev = make_state(room="105", step=0)
    step_frames = REFERENCE_STEP_FRAMES
    steps = CONTEMPT_GRACE_FRAMES // step_frames
    softlock_sum = 0.0
    for i in range(1, steps + 1):
        cur = make_state(room="105", step=i)
        _, bd = _step(progress, prev, cur, step_frames=step_frames)
        softlock_sum += bd["softlock"]
        prev = cur
    assert progress.stagnation_frames == steps * step_frames
    assert progress.stagnation_frames <= CONTEMPT_GRACE_FRAMES
    assert softlock_sum == pytest.approx(0.0)


def test_start_budget_truncates_at_six_minutes_with_grace_ramp():
    """Episode starts at 12 min idle cap; 3 min grace then ramp to full contempt."""
    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    step_frames = REFERENCE_STEP_FRAMES
    threshold = SOFTLOCK_PRE_KENNETH_FRAMES
    steps = threshold // step_frames
    softlock_sum = 0.0
    for i in range(1, steps + 1):
        cur = make_state(room="105", step=i)
        _, bd = _step(progress, prev, cur, step_frames=step_frames)
        softlock_sum += bd["softlock"]
        prev = cur
    assert softlock_frame_threshold(progress) == threshold
    assert stagnation_episode_timeout(progress)
    assert softlock_sum == pytest.approx(SOFTLOCK_TIMEOUT_PENALTY, rel=1e-5)
    assert -softlock_sum == pytest.approx(CONTEMPT_BUDGET_SCALED, rel=1e-5)


def test_ramp_integral_equals_contempt_budget_post_kenneth():
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.rewarded_cutscenes.add("104:0:s0")
    prev = make_state(room="105", step=0)
    softlock_sum = 0.0
    threshold = SOFTLOCK_POST_KENNETH_FRAMES
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


def test_contempt_spent_quadratic_mid_ramp():
    grace = CONTEMPT_GRACE_FRAMES
    threshold = SOFTLOCK_POST_KENNETH_FRAMES
    ramp = threshold - grace
    mid = grace + ramp // 2
    spent = contempt_spent_at(mid, grace=grace, threshold=threshold)
    assert spent == pytest.approx(CONTEMPT_BUDGET_SCALED * 0.25, rel=1e-6)
    assert contempt_spent_at(grace, grace=grace, threshold=threshold) == pytest.approx(
        0.0
    )
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


def test_softlock_contempt_weaker_than_death():
    assert abs(DEATH_PENALTY) > CONTEMPT_BUDGET_SCALED
    assert abs(DEATH_PENALTY) == pytest.approx(5.0 * CONTEMPT_BUDGET_SCALED)


def test_progress_resets_ramp():
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.rewarded_cutscenes.add("104:0:s0")
    prev = make_state(room="105", step=0)
    # Sit past grace into the ramp.
    progress._stagnation_frames = CONTEMPT_GRACE_FRAMES + 600
    assert (
        contempt_spent_at(
            progress.stagnation_frames,
            threshold=SOFTLOCK_POST_KENNETH_FRAMES,
        )
        > 0.0
    )
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


def _health_pickup_breakdown(
    item: str,
    *,
    before: list[tuple[str, int]],
    after: list[tuple[str, int]],
    rails_mode: bool = False,
) -> dict[str, float]:
    prev = make_state(
        room="105",
        step=0,
        inventory=[name for name, _ in before],
        inventory_slots=before,
    )
    cur = make_state(
        room="105",
        step=1,
        inventory=[name for name, _ in after],
        inventory_slots=after,
        new_items=[item],
    )
    _, bd = compute_reward(
        prev,
        cur,
        make_planner(),
        progress=ProgressTracker(),
        rails_mode=rails_mode,
        return_breakdown=True,
    )
    return bd


def test_health_pickup_crumb_requires_missing_resource_class() -> None:
    filler = [("knife", 0), ("beretta", 15), ("emblem", 1), ("lockpick", 1)]
    first_hp = _health_pickup_breakdown(
        "green_herb", before=filler, after=filler + [("green_herb", 1)]
    )
    assert first_hp["item"] == ITEM_PICKUP_BONUS

    before_hp = filler[:3] + [("green_herb", 1)]
    duplicate_hp = _health_pickup_breakdown(
        "red_herb",
        before=before_hp,
        after=before_hp + [("red_herb", 1)],
    )
    assert duplicate_hp["item"] == 0.0

    independent_cure = _health_pickup_breakdown(
        "blue_herb",
        before=before_hp,
        after=before_hp + [("blue_herb", 1)],
    )
    assert independent_cure["item"] == ITEM_PICKUP_BONUS


def test_dual_purpose_health_pickup_can_supply_either_missing_class() -> None:
    base = [("knife", 0), ("beretta", 15), ("emblem", 1)]
    for held in ("green_herb", "blue_herb"):
        before = base + [(held, 1)]
        bd = _health_pickup_breakdown(
            "mixed_herbs_gb",
            before=before,
            after=before + [("mixed_herbs_gb", 1)],
        )
        assert bd["item"] == ITEM_PICKUP_BONUS

    before_both = base + [("mixed_herbs_gb", 1)]
    duplicate = _health_pickup_breakdown(
        "mixed_herbs_grb",
        before=before_both,
        after=before_both + [("mixed_herbs_grb", 1)],
    )
    assert duplicate["item"] == 0.0


def test_health_pickup_capacity_gate_precedes_rails_scale() -> None:
    four = [("knife", 0), ("beretta", 15), ("emblem", 1), ("lockpick", 1)]
    three_free = _health_pickup_breakdown(
        "red_herb",
        before=four,
        after=four + [("red_herb", 1)],
        rails_mode=True,
    )
    assert three_free["item"] == pytest.approx(ITEM_PICKUP_BONUS * 0.05)

    five = four + [("shotgun", 1)]
    two_free = _health_pickup_breakdown(
        "red_herb",
        before=five,
        after=five + [("red_herb", 1)],
    )
    assert two_free["item"] == 0.0


def test_ammo_pickup_extends_idle_cap_and_clears_stagnation_clock():
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    progress.claim_spawn_room_bonus()
    progress.note_stagnation_step(made_progress=False, step_frames=20)
    prev = make_state(room="105", step=0)
    cur = make_state(room="105", step=1, new_items=["handgun_bullets"])
    _, bd = _step(progress, prev, cur, step_frames=4)
    assert bd["ammo_pickup"] == AMMO_PICKUP_BONUS
    assert bd["item"] == 0.0
    # note_softlock_extension clears the idle clock, then this step ticks forward.
    assert progress.stagnation_frames == 4
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES
    assert softlock_frame_threshold(progress) == SOFTLOCK_EXTENSION_FRAMES


def test_weapon_pickup_resets_idle_timer_and_raises_six_minute_cap():
    from re1_rl.reward import NEW_WEAPON_PICKUP_BONUS, SOFTLOCK_EXTENSION_FRAMES

    progress = ProgressTracker()
    progress.first_visit("105")
    prev = make_state(room="105", step=0)
    for i in range(1, 4):
        cur = make_state(room="105", step=i)
        _step(progress, prev, cur, step_frames=4)
        prev = cur
    assert progress.stagnation_frames == 12
    assert softlock_frame_threshold(progress) == SOFTLOCK_PRE_KENNETH_FRAMES
    cur = make_state(room="115", step=4, new_items=["colt_python"])
    _, bd = _step(progress, prev, cur, step_frames=4)

    assert bd["new_weapon"] == NEW_WEAPON_PICKUP_BONUS == 4.0
    assert bd["item"] == 0.0
    assert bd["key_item"] == 0.0
    assert progress.stagnation_frames == 0
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES
    assert softlock_frame_threshold(progress) == SOFTLOCK_EXTENSION_FRAMES


def test_beretta_inventory_flicker_does_not_repay_weapon():
    from re1_rl.reward import NEW_WEAPON_PICKUP_BONUS

    progress = ProgressTracker()
    progress.first_visit("111")
    progress.claim_weapon_progress("beretta")
    prev = make_state(
        room="111",
        step=65,
        inventory=["knife", "beretta"],
    )
    cur = make_state(
        room="111",
        step=66,
        inventory=["knife", "beretta"],
        new_items=["beretta"],
    )
    _, bd = _step(progress, prev, cur, step_frames=4)
    assert bd["new_weapon"] == 0.0
    assert bd["key_item"] == 0.0


def test_bazooka_ammo_swap_does_not_repay_new_weapon():
    from re1_rl.reward import NEW_WEAPON_PICKUP_BONUS

    progress = ProgressTracker()
    progress.first_visit("202")
    prev = make_state(
        room="202",
        step=10,
        inventory=["knife", "beretta", "bazooka_acid"],
    )
    progress.claim_weapon_progress("bazooka_acid")
    cur = make_state(
        room="202",
        step=11,
        inventory=["knife", "beretta", "bazooka_explosive"],
        new_items=["bazooka_explosive"],
    )
    _, bd = _step(progress, prev, cur, step_frames=4)
    assert bd["new_weapon"] == 0.0

    fresh = ProgressTracker()
    fresh.first_visit("212")
    empty = make_state(room="212", step=0, inventory=["knife", "beretta"])
    first = make_state(
        room="212",
        step=1,
        inventory=["knife", "beretta", "bazooka_acid"],
        new_items=["bazooka_acid"],
    )
    _, first_bd = _step(fresh, empty, first, step_frames=4)
    assert first_bd["new_weapon"] == NEW_WEAPON_PICKUP_BONUS == 4.0
    assert "bazooka" in fresh.weapons_progressed


def test_key_item_flicker_does_not_repay_bonus():
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.claim_key_item_bonus("emblem")
    prev = make_state(room="105", step=10, inventory=["knife", "beretta", "emblem"])
    cur = make_state(
        room="105",
        step=11,
        inventory=["knife", "beretta", "emblem"],
        new_items=["emblem"],
    )
    _, bd = _step(progress, prev, cur, step_frames=4)
    assert bd["key_item"] == 0.0


def test_shotgun_wall_loop_is_zero_sum_and_retake_does_not_refarm_idle():
    from re1_rl.reward import (
        NEW_WEAPON_PICKUP_BONUS,
        SHOTGUN_RETURN_PENALTY,
        SOFTLOCK_EXTENSION_FRAMES,
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
    assert pickup_bd["new_weapon"] == NEW_WEAPON_PICKUP_BONUS == 4.0
    assert pickup_bd["shotgun_return"] == 0.0
    assert progress.stagnation_frames == 0
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES
    assert SHOTGUN_RETURN_PENALTY == -NEW_WEAPON_PICKUP_BONUS == -4.0

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

    # Idle a bit, then re-take: still ±3 net zero, but no second idle extend/reset.
    progress._stagnation_frames = 400
    pickup2, pickup2_bd = _step(
        progress, returned, held, step_frames=0
    )
    assert pickup2_bd["new_weapon"] == NEW_WEAPON_PICKUP_BONUS
    assert progress.stagnation_frames == 400  # re-take is not made_progress
    return2, return2_bd = _step(
        progress, held, returned, step_frames=0
    )
    assert return2_bd["shotgun_return"] == SHOTGUN_RETURN_PENALTY
    assert pickup2 + return2 == 0.0
    # A live step after the loop still advances the idle clock (no free reset).
    _, _ = _step(progress, returned, returned, step_frames=8)
    assert progress.stagnation_frames == 408


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
    g = CONTEMPT_GRACE_FRAMES
    a = g + 1000
    b = g + 2000
    d1 = contempt_penalty_delta(g, a, threshold=SOFTLOCK_POST_KENNETH_FRAMES)
    d2 = contempt_penalty_delta(a, b, threshold=SOFTLOCK_POST_KENNETH_FRAMES)
    assert d1 < 0.0
    assert d2 < 0.0
    # Later slices of the linear-rate ramp are steeper.
    assert abs(d2) > abs(d1)
