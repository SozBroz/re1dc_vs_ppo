"""Surgical cp84 Richard cutscene (20D→204) support."""

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
from re1_rl.reward import (
    RAILS_CHECKPOINT_REWARD,
    WRONG_ROOM_TERMINAL_PENALTY,
    compute_reward,
)
from re1_rl.richard_cutscene_checkpoint import (
    RICHARD_CUTSCENE_KEY,
    richard_cutscene_capture_room_ok,
    richard_cutscene_skip_settled,
    note_richard_cutscene_skip_settle,
    should_suppress_wrong_room,
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
        "x": 3000,
        "y": 0,
        "z": 12600,
        "facing": 0,
        "hp": 96,
        "cam_id": 0,
        "character_id": 1,
        "in_control": True,
        "inventory": ["shield_key"],
        "dead": False,
        "step": 1,
    }
    s.update(kw)
    return s


def test_richard_cutscene_skip_20d_to_204_mints_ledger_key() -> None:
    planner = _planner("richard_cutscene_20D")
    progress = ProgressTracker()
    entry = _state("20D")
    new = _state("204")
    assert richard_cutscene_skip_settled(planner, entry, new, skip_frames=60)
    note_richard_cutscene_skip_settle(
        planner, progress, entry, new, skip_frames=60
    )
    assert RICHARD_CUTSCENE_KEY in progress.observed_cutscenes
    assert new.get("richard_cutscene_scripted_exit") is True


def test_richard_cutscene_skip_ignored_on_other_legs() -> None:
    planner = _planner("richard_room_enter_20D")
    entry = _state("20D")
    new = _state("204")
    assert not richard_cutscene_skip_settled(planner, entry, new, skip_frames=60)


def test_richard_cutscene_same_room_requires_duration_gate() -> None:
    planner = _planner("richard_cutscene_20D")
    entry = _state("20D")
    new = _state("20D")
    assert not richard_cutscene_skip_settled(
        planner, entry, new, skip_frames=MIN_CUTSCENE_SKIP_FRAMES - 1
    )
    assert richard_cutscene_skip_settled(
        planner, entry, new, skip_frames=MIN_CUTSCENE_SKIP_FRAMES
    )


def test_richard_cutscene_20d_to_204_no_terminal_wrong_room() -> None:
    g = RoomGraph(DOORS)
    planner = _planner("richard_cutscene_20D")
    progress = ProgressTracker()
    progress.observe_cutscene(RICHARD_CUTSCENE_KEY)
    prev = _state("20D")
    cur = _state("204")
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


def test_richard_cutscene_checkpoint_success_on_scripted_exit() -> None:
    g = RoomGraph(DOORS)
    planner = _planner("richard_cutscene_20D")
    progress = ProgressTracker()
    entry = _state("20D")
    cur = _state("204")
    note_richard_cutscene_skip_settle(
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


def test_richard_room_enter_20d_to_204_still_wrong_room() -> None:
    """Pillar-entry leg must not inherit Richard cutscene exemptions."""
    g = RoomGraph(DOORS)
    planner = _planner("richard_room_enter_20D")
    progress = ProgressTracker()
    _, bd = compute_reward(
        _state("20D"),
        _state("204"),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == WRONG_ROOM_TERMINAL_PENALTY
    assert progress.wrong_room_breached


def test_richard_cutscene_capture_room_ok_204() -> None:
    assert richard_cutscene_capture_room_ok("richard_cutscene_20D", "204", "20D")
    assert not richard_cutscene_capture_room_ok("richard_cutscene_20D", "203", "20D")
    assert not richard_cutscene_capture_room_ok("richard_room_enter_20D", "204", "20D")


def test_richard_cutscene_capture_in_room_204(tmp_path: Path) -> None:
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
    planner = _planner("richard_forced_return_204")
    progress = ProgressTracker()
    progress.observed_cutscenes.add(RICHARD_CUTSCENE_KEY)
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
        _read_state=lambda track_items=False: _state("204"),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {
            "schema_version": 1,
            "progress": {"observed_cutscenes": sorted(progress.observed_cutscenes)},
            "episode_history": {"room_entries": [["20D", 100], ["204", 200]]},
        },
    )
    proposal = capture_successor_cell(
        env,
        _state("204"),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert proposal is not None
    assert proposal["checkpoint_id"] == "richard_cutscene_20D"
    assert proposal["room_id"] == "204"
    monkeypatch.undo()


def test_should_suppress_wrong_room_on_scripted_exit_flag() -> None:
    planner = _planner("richard_cutscene_20D")
    assert should_suppress_wrong_room(
        planner, "20D", "204", {"richard_cutscene_scripted_exit": False}
    )
    assert should_suppress_wrong_room(
        planner,
        "20D",
        "203",
        {"richard_cutscene_scripted_exit": True},
    )
