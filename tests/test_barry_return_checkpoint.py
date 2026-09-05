"""Surgical cp02: Kenneth ``104:*:sN`` flag, heal spray held, then dining."""

from __future__ import annotations

import json
from pathlib import Path

from re1_rl.barry_return_checkpoint import (
    fail_barry_return_if_unmet,
    note_kenneth_cutscene_skip_settle,
    note_kenneth_live_scene,
)
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
        "inventory": [
            "knife",
            "beretta",
            "first_aid_spray_alt",
            "emblem",
            "handgun_bullets",
        ],
        "inventory_slots": [
            ("knife", 1),
            ("beretta", 15),
            ("first_aid_spray_alt", 1),
            ("emblem", 1),
            ("handgun_bullets", 30),
        ],
        "dead": False,
        "step": 1,
    }
    s.update(kw)
    return s


def test_dining_without_kenneth_kills_episode() -> None:
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
    assert bd["checkpoint_capture_ineligible"] == RAILS_CAPTURE_INELIGIBLE_PENALTY
    assert progress.capture_ineligible_breached


def test_kenneth_then_dining_return_creates_checkpoint() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    progress.observe_cutscene("104:0:s0")
    progress.note_leg_cutscene("104:0:s0")
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


def test_kenneth_then_dining_return_ignores_ammo_when_spray_held() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    progress.observe_cutscene("104:4:s0")
    progress.note_leg_cutscene("104:4:s0")
    bare = _state(
        "105",
        inventory=["knife", "beretta", "first_aid_spray_alt", "emblem", "handgun_bullets"],
        inventory_slots=[
            ("knife", 1),
            ("beretta", 14),
            ("first_aid_spray_alt", 1),
            ("emblem", 1),
            ("handgun_bullets", 30),
        ],
    )
    _, bd = compute_reward(
        _state("104"),
        bare,
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] > 0.0
    assert bd["checkpoint_capture_ineligible"] == 0.0
    assert not progress.capture_ineligible_breached


def test_dining_without_tea_clips_after_kenneth_kills_episode() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    progress.observe_cutscene("104:0:s0")
    progress.note_leg_cutscene("104:0:s0")
    progress.note_leg_room_transition("104", "105")
    _, bd = compute_reward(
        _state("104"),
        _state(
            "105",
            inventory=["knife", "beretta", "first_aid_spray_alt", "emblem"],
            inventory_slots=[
                ("knife", 1),
                ("beretta", 15),
                ("first_aid_spray_alt", 1),
                ("emblem", 1),
            ],
        ),
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] == 0.0
    assert bd["checkpoint_capture_ineligible"] == RAILS_CAPTURE_INELIGIBLE_PENALTY
    assert progress.capture_ineligible_breached


def test_dining_without_heal_spray_after_kenneth_kills_episode() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    progress.observe_cutscene("104:0:s0")
    progress.note_leg_cutscene("104:0:s0")
    progress.note_leg_room_transition("104", "105")
    _, bd = compute_reward(
        _state("104"),
        _state(
            "105",
            inventory=["knife", "beretta", "emblem"],
            inventory_slots=[
                ("knife", 1),
                ("beretta", 15),
                ("emblem", 1),
            ],
        ),
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] == 0.0
    assert bd["checkpoint_capture_ineligible"] == RAILS_CAPTURE_INELIGIBLE_PENALTY
    assert progress.capture_ineligible_breached


def test_inherited_kenneth_flag_does_not_create_checkpoint() -> None:
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
        state=_state("104"),
    )
    assert bd["checkpoint_success"] == 0.0
    assert "checkpoint_capture_ineligible" not in bd


def test_note_kenneth_skip_settle_from_dining_door() -> None:
    """Door+Kenneth skip starts in 105; flag is 104:{settle_cam}:sN."""
    progress = ProgressTracker()
    entry = _state("105", cam_id=2, scene_flag=0x80)
    settle = _state("104", cam_id=4, scene_flag=0x84)
    assert (
        note_kenneth_cutscene_skip_settle(
            progress, entry, settle, skip_frames=880, peak_scene_flag=0x84
        )
        == "104:4:s0"
    )
    assert "104:4:s0" in progress.observed_cutscenes
    assert "104:4:s0" in progress.leg_observed_cutscenes
    assert (
        note_kenneth_cutscene_skip_settle(
            progress, entry, settle, skip_frames=880, peak_scene_flag=0x84
        )
        is None
    )


def test_note_kenneth_ignores_idle_tea_room_skip() -> None:
    progress = ProgressTracker()
    entry = _state("105", cam_id=2, scene_flag=0x80)
    idle = _state("104", cam_id=0, scene_flag=0x80)
    assert (
        note_kenneth_cutscene_skip_settle(
            progress, entry, idle, skip_frames=880, peak_scene_flag=0x80
        )
        is None
    )
    assert not progress.observed_cutscenes


def test_note_kenneth_latches_zero_frame_peak_script() -> None:
    """Turbo Kenneth can burn 0 Python frames; 0x84 only exists as a skip peak."""
    progress = ProgressTracker()
    entry = _state("104", cam_id=4, scene_flag=0x80)
    settle = _state("104", cam_id=4, scene_flag=0x80)
    assert (
        note_kenneth_cutscene_skip_settle(
            progress, entry, settle, skip_frames=0, peak_scene_flag=0x84
        )
        == "104:4:s0"
    )


def test_live_scene_uses_skip_peak_when_settle_is_idle() -> None:
    progress = ProgressTracker()
    settle = _state("104", cam_id=4, scene_flag=0x80)
    settle["_skip_peak_scene_flag"] = 0x84
    assert note_kenneth_live_scene(progress, settle) == "104:4:s0"
    assert "104:4:s0" in progress.observed_cutscenes


def test_live_tea_room_script_writes_kenneth_flag() -> None:
    planner = _planner("barry_return_105")
    progress = ProgressTracker()
    _, bd = compute_reward(
        _state("104", scene_flag=0x80),
        _state("104", cam_id=4, scene_flag=0x84),
        planner,
        progress=progress,
        rails_mode=True,
        return_breakdown=True,
    )
    assert "104:4:s0" in progress.observed_cutscenes
    assert bd["checkpoint_success"] == 0.0
    assert note_kenneth_live_scene(progress, _state("104", cam_id=4, scene_flag=0x84)) is None
