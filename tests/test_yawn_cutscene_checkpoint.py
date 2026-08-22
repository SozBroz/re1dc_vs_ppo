"""Surgical cp120 Yawn intro cinema (same-room 210) support."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES
from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import RAILS_CHECKPOINT_REWARD, compute_reward
from re1_rl.room_graph import RoomGraph
from re1_rl.yawn_cutscene_checkpoint import (
    YAWN_CUTSCENE_KEY,
    note_yawn_cutscene_skip_settle,
    yawn_cutscene_seen,
    yawn_cutscene_skip_settled,
)

YAWN_ROUTE = PROJECT_ROOT / "data" / "yawn_checkpoint_route.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"
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
        "x": 8000,
        "y": 0,
        "z": 4000,
        "facing": 0,
        "hp": 96,
        "cam_id": 0,
        "character_id": 1,
        "in_control": True,
        "inventory": ["shield_key", "shotgun"],
        "dead": False,
        "step": 1,
    }
    s.update(kw)
    return s


def test_yawn_cutscene_seen_requires_ledger_not_door_cam() -> None:
    progress = ProgressTracker()
    assert not yawn_cutscene_seen(progress)
    progress.observe_cutscene("210:0:s0")
    assert not yawn_cutscene_seen(progress)
    progress.observe_cutscene(YAWN_CUTSCENE_KEY)
    assert yawn_cutscene_seen(progress)


def test_yawn_cutscene_does_not_advance_on_generic_210_cam() -> None:
    planner = _planner("yawn_cutscene_210")
    progress = ProgressTracker()
    progress.observe_cutscene("210:0:s0")
    assert not planner.advance_if_success(_state("210"), progress=progress)


def test_yawn_cutscene_skip_ignored_on_arena_enter_leg() -> None:
    planner = _planner("yawn_arena_enter_210")
    entry = _state("210")
    new = _state("210")
    assert not yawn_cutscene_skip_settled(
        planner, entry, new, skip_frames=MIN_CUTSCENE_SKIP_FRAMES
    )


def test_yawn_cutscene_short_skip_never_mints() -> None:
    planner = _planner("yawn_cutscene_210")
    assert not yawn_cutscene_skip_settled(
        planner,
        _state("210"),
        _state("210"),
        skip_frames=MIN_CUTSCENE_SKIP_FRAMES - 1,
    )


def test_yawn_cutscene_wrong_room_skip_never_mints() -> None:
    planner = _planner("yawn_cutscene_210")
    assert not yawn_cutscene_skip_settled(
        planner,
        _state("20E"),
        _state("210"),
        skip_frames=MIN_CUTSCENE_SKIP_FRAMES,
    )


def test_yawn_cutscene_long_same_room_skip_mints() -> None:
    planner = _planner("yawn_cutscene_210")
    progress = ProgressTracker()
    entry = _state("210")
    cur = _state("210")
    note_yawn_cutscene_skip_settle(
        planner,
        progress,
        entry,
        cur,
        skip_frames=MIN_CUTSCENE_SKIP_FRAMES,
    )
    assert YAWN_CUTSCENE_KEY in progress.observed_cutscenes
    assert cur.get("yawn_cutscene_confirmed") is True


def test_yawn_cutscene_checkpoint_success_on_skip_settle() -> None:
    g = RoomGraph(DOORS)
    planner = _planner("yawn_cutscene_210")
    progress = ProgressTracker()
    entry = _state("210")
    cur = _state("210")
    note_yawn_cutscene_skip_settle(
        planner,
        progress,
        entry,
        cur,
        skip_frames=2400,
    )
    _, bd = compute_reward(
        entry,
        cur,
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] == RAILS_CHECKPOINT_REWARD
    assert progress.checkpoint_success


def test_yawn_cutscene_generic_cam_does_not_pay() -> None:
    g = RoomGraph(DOORS)
    planner = _planner("yawn_cutscene_210")
    progress = ProgressTracker()
    progress.observe_cutscene("210:0:s0")
    _, bd = compute_reward(
        _state("210"),
        _state("210"),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] == 0.0
    assert not progress.checkpoint_success
