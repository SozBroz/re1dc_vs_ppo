"""Surgical cp25 Barry rescue cutscene (115→109) support."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.barry_rescue_checkpoint import (
    BARRY_RESCUE_CUTSCENE_KEY,
    barry_rescue_capture_room_ok,
    barry_rescue_skip_settled,
    note_barry_rescue_skip_settle,
    should_suppress_wrong_room,
)
from re1_rl.cutscene_reward import MIN_CUTSCENE_SKIP_FRAMES
from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    RAILS_CHECKPOINT_REWARD,
    WRONG_ROOM_TERMINAL_PENALTY,
    compute_reward,
)
from re1_rl.room_graph import RoomGraph

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
        "x": 30000,
        "y": 0,
        "z": 7500,
        "facing": 0,
        "hp": 96,
        "cam_id": 0,
        "character_id": 1,
        "in_control": True,
        "inventory": [],
        "dead": False,
        "step": 1,
    }
    s.update(kw)
    return s


def test_barry_rescue_skip_115_to_109_mints_ledger_key() -> None:
    planner = _planner("barry_rescue_115")
    progress = ProgressTracker()
    entry = _state("115", inventory=["shotgun"])
    new = _state("109", inventory=["shotgun"])
    assert barry_rescue_skip_settled(planner, entry, new, skip_frames=60)
    note_barry_rescue_skip_settle(
        planner, progress, entry, new, skip_frames=60
    )
    assert BARRY_RESCUE_CUTSCENE_KEY in progress.observed_cutscenes
    assert new.get("barry_rescue_scripted_exit") is True


def test_barry_rescue_skip_ignored_on_other_legs() -> None:
    planner = _planner("barry_reenter_115")
    entry = _state("115", inventory=["shotgun"])
    new = _state("109", inventory=["shotgun"])
    assert not barry_rescue_skip_settled(planner, entry, new, skip_frames=60)


def test_barry_rescue_same_room_requires_duration_gate() -> None:
    planner = _planner("barry_rescue_115")
    entry = _state("115", inventory=["shotgun"])
    new = _state("115", inventory=["shotgun"])
    assert not barry_rescue_skip_settled(
        planner, entry, new, skip_frames=MIN_CUTSCENE_SKIP_FRAMES - 1
    )
    assert barry_rescue_skip_settled(
        planner, entry, new, skip_frames=MIN_CUTSCENE_SKIP_FRAMES
    )


def test_barry_rescue_115_to_109_no_terminal_wrong_room() -> None:
    g = RoomGraph(DOORS)
    planner = _planner("barry_rescue_115")
    progress = ProgressTracker()
    progress.observe_cutscene(BARRY_RESCUE_CUTSCENE_KEY)
    prev = _state("115", inventory=["shotgun"])
    cur = _state("109", inventory=["shotgun"])
    _, bd = compute_reward(
        prev,
        cur,
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == 0.0
    assert not progress.wrong_room_breached


def test_barry_rescue_checkpoint_success_on_scripted_exit() -> None:
    g = RoomGraph(DOORS)
    planner = _planner("barry_rescue_115")
    progress = ProgressTracker()
    entry = _state("115", inventory=["shotgun"])
    cur = _state("109", inventory=["shotgun"])
    note_barry_rescue_skip_settle(
        planner, progress, entry, cur, skip_frames=120
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
    assert bd["wrong_room"] == 0.0
    assert bd["checkpoint_success"] == RAILS_CHECKPOINT_REWARD
    assert progress.checkpoint_success


def test_trap_entry_115_to_109_still_wrong_room() -> None:
    """Earlier shotgun-trap leg must not inherit Barry rescue exemptions."""
    g = RoomGraph(DOORS)
    planner = _planner("trap_entry_115")
    progress = ProgressTracker()
    _, bd = compute_reward(
        _state("115", inventory=["shotgun"]),
        _state("109", inventory=["shotgun"]),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == WRONG_ROOM_TERMINAL_PENALTY
    assert progress.wrong_room_breached


def test_barry_rescue_capture_room_ok_109() -> None:
    assert barry_rescue_capture_room_ok("barry_rescue_115", "109", "115")
    assert not barry_rescue_capture_room_ok("barry_rescue_115", "106", "115")
    assert not barry_rescue_capture_room_ok("trap_entry_115", "109", "115")


def test_barry_rescue_capture_in_room_109(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    from re1_rl.reward import RAILS_CHECKPOINT_REWARD
    from re1_rl.yawn_rails import capture_successor_cell

    bridge = MagicMock()
    bridge.save_savestate.side_effect = lambda path: Path(path).write_bytes(
        b"state"
    )
    planner = _planner("back_passage_10A")
    progress = ProgressTracker()
    progress.observed_cutscenes.add(BARRY_RESCUE_CUTSCENE_KEY)
    env = SimpleNamespace(
        project_root=tmp_path,
        _stage={
            "mode": "yawn_rails",
            "cells_manifest": "states/yawn_rails/manifest.json",
            "route_id": "test",
        },
        _planner=planner,
        bridge=bridge,
        _macro_active=False,
        _progress=progress,
        _step_count=300,
        _read_state=lambda track_items=False: _state("109", inventory=["shotgun"]),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {
            "schema_version": 1,
            "progress": {"observed_cutscenes": sorted(progress.observed_cutscenes)},
            "episode_history": {"room_entries": [["115", 100], ["109", 200]]},
        },
    )
    proposal = capture_successor_cell(
        env,
        _state("109", inventory=["shotgun"]),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert proposal is not None
    assert proposal["checkpoint_id"] == "barry_rescue_115"
    assert proposal["room_id"] == "109"
    monkeypatch.undo()
