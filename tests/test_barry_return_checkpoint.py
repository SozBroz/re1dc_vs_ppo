"""Surgical cp02 Barry-return gate: Kenneth ledger, not a dining-door mint."""

from __future__ import annotations

import json
from pathlib import Path

from re1_rl.barry_return_checkpoint import fail_barry_return_if_unmet
from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import RAILS_CAPTURE_INELIGIBLE_PENALTY, compute_reward

PROJECT_ROOT = Path(__file__).resolve().parents[1]
YAWN_ROUTE = PROJECT_ROOT / "data" / "yawn_checkpoint_route.json"
_ROUTE = json.loads(YAWN_ROUTE.read_text(encoding="utf-8"))
_ROUTE_N = len(_ROUTE)
_ROUTE_INDEX = {str(row["checkpoint_id"]): i for i, row in enumerate(_ROUTE)}


def _planner(checkpoint_id: str) -> WaypointPlanner:
    return WaypointPlanner(
        YAWN_ROUTE,
        route_steps=list(range(1, _ROUTE_N + 1)),
        start_index=int(_ROUTE_INDEX[checkpoint_id]),
    )


def _state(room: str, **kw) -> dict:
    s = {
        "room_id": room,
        "x": 30000,
        "y": 0,
        "z": 7500,
        "facing": 0,
        "hp": 96,
        "cam_id": 2,
        "character_id": 1,
        "in_control": True,
        "inventory": ["knife", "beretta", "first_aid_spray_alt", "emblem"],
        "inventory_slots": [
            ("knife", 1),
            ("beretta", 15),
            ("first_aid_spray_alt", 1),
            ("emblem", 1),
        ],
        "dead": False,
        "step": 1,
    }
    s.update(kw)
    return s


def test_door_bounce_without_kenneth_does_not_complete_or_fail() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    progress.observe_cutscene("105:barry_return")
    _, bd = compute_reward(
        _state("104"),
        _state("105"),
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] == 0.0
    assert bd["checkpoint_capture_ineligible"] == 0.0
    assert not progress.capture_ineligible_breached


def test_kenneth_then_dining_return_creates_checkpoint() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    progress.observe_cutscene("104:0:s0")
    _, bd = compute_reward(
        _state("104"),
        _state("105"),
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] > 0.0
    assert bd["checkpoint_capture_ineligible"] == 0.0
    assert not progress.capture_ineligible_breached


def test_kenneth_then_dining_return_wrong_ammo_kills_episode() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    progress.observe_cutscene("104:0:s0")
    bad = _state(
        "105",
        inventory_slots=[
            ("knife", 1),
            ("beretta", 14),
            ("first_aid_spray_alt", 1),
            ("emblem", 1),
        ],
    )
    _, bd = compute_reward(
        _state("104"),
        bad,
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] == 0.0
    assert bd["checkpoint_capture_ineligible"] == RAILS_CAPTURE_INELIGIBLE_PENALTY
    assert progress.capture_ineligible_breached


def test_kenneth_still_in_tea_room_does_not_fail() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    progress.observe_cutscene("104:0:s0")
    bd: dict[str, float] = {"checkpoint_success": 0.0}
    assert not fail_barry_return_if_unmet(
        planner,
        progress,
        bd,
        RAILS_CAPTURE_INELIGIBLE_PENALTY,
        room_id="104",
    )
    assert bd["checkpoint_success"] == 0.0
    assert "checkpoint_capture_ineligible" not in bd
