"""Document/file examine UI: +3 on rising edge, once per room, extends idle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.memory_map import (
    DOCUMENT_EXAMINE_GAME_MODE,
    DOCUMENT_EXAMINE_GAME_STATE,
    IN_CONTROL_MASK,
)
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    NEW_DOCUMENT_EXAMINE_BONUS,
    NEW_ROOM_BONUS,
    SOFTLOCK_EXTENSION_FRAMES,
    compute_reward,
)
from tests.test_scaffolding import make_planner, make_state


def _play(**kw):
    return make_state(
        game_mode=IN_CONTROL_MASK,
        game_state=0x80800000,
        in_control=True,
        **kw,
    )


def _document(room: str = "105", **kw):
    return make_state(
        room=room,
        game_mode=DOCUMENT_EXAMINE_GAME_MODE,
        game_state=DOCUMENT_EXAMINE_GAME_STATE,
        in_control=False,
        **kw,
    )


def _step(progress, prev, cur):
    return compute_reward(
        prev,
        cur,
        make_planner(),
        progress=progress,
        return_breakdown=True,
    )


def test_first_edge_into_document_pays_plus_three_and_extends():
    """Rising edge into gs=0x40808100 pays +3 and floors idle cap at 12 min."""
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.claim_spawn_room_bonus()  # consume spawn credit
    prev = _play(room="105", step=0)
    cur = _document(room="105", step=1)
    _, bd = _step(progress, prev, cur)
    assert bd["document_examine"] == NEW_DOCUMENT_EXAMINE_BONUS
    assert bd["document_examine"] == pytest.approx(3.0)
    assert bd["new_room"] == 0.0
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES
    assert progress.stagnation_frames == 0
    assert "105" in progress.rewarded_document_rooms


def test_staying_in_document_ui_does_not_repay():
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.claim_spawn_room_bonus()
    prev = _play(room="105", step=0)
    open_ui = _document(room="105", step=1)
    _, bd0 = _step(progress, prev, open_ui)
    assert bd0["document_examine"] == NEW_DOCUMENT_EXAMINE_BONUS

    still = _document(room="105", step=2)
    _, bd1 = _step(progress, open_ui, still)
    assert bd1["document_examine"] == 0.0
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES


def test_leave_and_reenter_same_room_does_not_repay():
    """Anti-farm: once per room per episode (no stable document ID in RAM)."""
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.claim_spawn_room_bonus()

    _, bd0 = _step(progress, _play(room="105", step=0), _document(room="105", step=1))
    assert bd0["document_examine"] == NEW_DOCUMENT_EXAMINE_BONUS

    # Dismiss then reopen in the same room — no second pay.
    _, bd1 = _step(
        progress,
        _document(room="105", step=2),
        _play(room="105", step=3),
    )
    assert bd1["document_examine"] == 0.0

    _, bd2 = _step(
        progress,
        _play(room="105", step=4),
        _document(room="105", step=5),
    )
    assert bd2["document_examine"] == 0.0


def test_document_in_different_unpaid_room_pays_again():
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.first_visit("104")
    progress.claim_spawn_room_bonus()

    _, bd0 = _step(progress, _play(room="105", step=0), _document(room="105", step=1))
    assert bd0["document_examine"] == NEW_DOCUMENT_EXAMINE_BONUS

    _, bd1 = _step(progress, _play(room="104", step=2), _document(room="104", step=3))
    assert bd1["document_examine"] == NEW_DOCUMENT_EXAMINE_BONUS
    assert progress.rewarded_document_rooms == {"105", "104"}


def test_item_grid_is_not_document_examine():
    """ITEM gs=0x40808000 must not pay the document channel."""
    progress = ProgressTracker()
    progress.first_visit("105")
    progress.claim_spawn_room_bonus()
    prev = _play(room="105", step=0)
    item = make_state(
        room="105",
        step=1,
        game_mode=DOCUMENT_EXAMINE_GAME_MODE,
        game_state=0x40808000,
        in_control=False,
    )
    _, bd = _step(progress, prev, item)
    assert bd["document_examine"] == 0.0
    assert NEW_DOCUMENT_EXAMINE_BONUS == NEW_ROOM_BONUS
