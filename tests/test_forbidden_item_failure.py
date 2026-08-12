"""Terminal failure when Jill picks up forbidden Yawn-route items."""

from __future__ import annotations

import pytest

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import FORBIDDEN_ITEM_TERMINAL_PENALTY, compute_reward
from tests.test_yawn_rails import ROUTE, _graph, _planner, _state


def test_broken_shotgun_pickup_terminates_rails_episode() -> None:
    progress = ProgressTracker()
    progress.seed_spawn_room("103")
    planner = _planner(start_index=64)  # vacant_enter_102
    prev = _state("102", inventory=["beretta", "shotgun", "shield_key"])
    cur = _state(
        "102",
        inventory=["beretta", "shotgun", "shield_key", "broken_shotgun"],
        new_items=["broken_shotgun"],
    )
    reward, bd = compute_reward(
        prev,
        cur,
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["forbidden_item"] == pytest.approx(FORBIDDEN_ITEM_TERMINAL_PENALTY)
    assert progress.forbidden_item_breached
    assert reward < 0.0
    assert bd["new_room"] == 0.0


def test_broken_shotgun_not_penalized_off_rails() -> None:
    progress = ProgressTracker()
    prev = _state("102", inventory=["shotgun"])
    cur = _state(
        "102",
        inventory=["shotgun", "broken_shotgun"],
        new_items=["broken_shotgun"],
    )
    _, bd = compute_reward(
        prev,
        cur,
        WaypointPlanner(ROUTE),
        progress=progress,
        graph=_graph(),
        rails_mode=False,
        return_breakdown=True,
    )
    assert bd["forbidden_item"] == 0.0
    assert not progress.forbidden_item_breached
