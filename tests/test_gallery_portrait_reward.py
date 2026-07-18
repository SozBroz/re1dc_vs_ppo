"""Dense, clawback-safe reward and hints for Gallery room 117."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.gallery_puzzle import (
    GALLERY_EXIT_TARGET,
    GALLERY_STEP_REWARD,
    GALLERY_STEP_VALUES,
    GALLERY_TARGETS,
    encode_gallery_hint,
)
from re1_rl.obs_encoder import GOAL_FIELDS
from re1_rl.progress import ProgressTracker
from re1_rl.reward import compute_reward
from tests.test_scaffolding import make_planner, make_state


def _state(*, room: str = "117", raw: int = 0, confirm: int = 0, inventory=()):
    return make_state(
        room=room,
        x=2900,
        z=10000,
        facing=0,
        gallery_progress=raw,
        gallery_confirm=confirm,
        inventory=list(inventory),
        new_items=[],
    )


def _reward(progress: ProgressTracker, prev: dict, state: dict):
    return compute_reward(
        prev,
        state,
        make_planner(),
        progress=progress,
        return_breakdown=True,
    )


def test_all_six_ordered_switches_pay_and_extend() -> None:
    progress = ProgressTracker()
    progress.first_visit("117")
    progress._stagnation_frames = 100
    prev = _state()
    for index, raw in enumerate(GALLERY_STEP_VALUES, start=1):
        state = _state(raw=raw)
        _total, bd = _reward(progress, prev, state)
        assert bd["gallery"] == pytest.approx(GALLERY_STEP_REWARD)
        assert progress.gallery_step_index == index
        assert progress.gallery_pending_reward == pytest.approx(
            index * GALLERY_STEP_REWARD
        )
        assert progress.stagnation_frames == 0
        prev = state


def test_wrong_switch_claws_back_every_pending_gallery_reward() -> None:
    progress = ProgressTracker()
    progress.first_visit("117")
    first = _state(raw=GALLERY_STEP_VALUES[0])
    second = _state(raw=GALLERY_STEP_VALUES[1])
    _reward(progress, _state(), first)
    _reward(progress, first, second)

    reset = _state(raw=0)
    _total, bd = _reward(progress, second, reset)
    assert bd["gallery"] == pytest.approx(-2 * GALLERY_STEP_REWARD)
    assert progress.gallery_pending_reward == 0.0
    assert progress.gallery_step_index == 0

    _total, retry_bd = _reward(progress, reset, first)
    assert retry_bd["gallery"] == 0.0
    assert progress.gallery_needs_reentry

    outside = _state(room="10A")
    _reward(progress, first, outside)
    reentered = _state()
    _reward(progress, outside, reentered)
    _total, retry_bd = _reward(progress, reentered, first)
    assert retry_bd["gallery"] == pytest.approx(GALLERY_STEP_REWARD)


def test_leaving_gallery_claws_back_partial_sequence() -> None:
    progress = ProgressTracker()
    progress.first_visit("117")
    first = _state(raw=GALLERY_STEP_VALUES[0])
    _reward(progress, _state(), first)

    outside = _state(room="106", raw=GALLERY_STEP_VALUES[0])
    _total, bd = _reward(progress, first, outside)
    assert bd["gallery"] == pytest.approx(-GALLERY_STEP_REWARD)
    assert progress.gallery_pending_reward == 0.0
    assert progress.gallery_needs_reentry

    reentered = _state()
    _reward(progress, outside, reentered)
    assert not progress.gallery_needs_reentry


def test_wrong_first_confirmation_locks_rewards_and_points_to_exit() -> None:
    progress = ProgressTracker()
    progress.first_visit("117")
    before = _state()
    wrong = _state(confirm=2)
    _total, bd = _reward(progress, before, wrong)
    assert bd["gallery"] == 0.0
    assert progress.gallery_needs_reentry

    x, z = GALLERY_EXIT_TARGET
    hint = encode_gallery_hint(
        wrong | {"x": x, "z": z, "gallery_needs_reentry": True}
    )
    assert hint[2] == pytest.approx(0.0)
    assert hint[3] == -1.0

    first = _state(raw=GALLERY_STEP_VALUES[0], confirm=2)
    _total, locked_bd = _reward(progress, wrong, first)
    assert locked_bd["gallery"] == 0.0


def test_star_crest_finalizes_sequence_without_gallery_double_pay() -> None:
    progress = ProgressTracker()
    progress.first_visit("117")
    prev = _state()
    for raw in GALLERY_STEP_VALUES:
        state = _state(raw=raw)
        _reward(progress, prev, state)
        prev = state

    crest = _state(raw=GALLERY_STEP_VALUES[-1], inventory=("star_crest",))
    crest["new_items"] = ["star_crest"]
    _total, crest_bd = _reward(progress, prev, crest)
    assert crest_bd["gallery"] == 0.0
    assert progress.gallery_completed
    assert progress.gallery_pending_reward == 0.0

    outside = _state(room="106", inventory=("star_crest",))
    _total, leave_bd = _reward(progress, crest, outside)
    assert leave_bd["gallery"] == 0.0


def test_gallery_hint_uses_existing_four_goal_slots() -> None:
    assert [name for name, _ in GOAL_FIELDS[-4:]] == [
        "gallery_bearing_sin",
        "gallery_bearing_cos",
        "gallery_distance",
        "gallery_progress",
    ]
    assert np.array_equal(encode_gallery_hint(_state(room="106")), np.zeros(4))

    x, z = GALLERY_TARGETS[1]
    hint = encode_gallery_hint(
        _state(raw=GALLERY_STEP_VALUES[0]) | {"x": x, "z": z}
    )
    assert hint[2] == pytest.approx(0.0)
    assert hint[3] == pytest.approx(1.0 / 6.0)
