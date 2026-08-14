"""cp46 (upper hall enter) requires clearing both C Passage zombies in 204."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import RAILS_CHECKPOINT_REWARD
from re1_rl.yawn_rails import capture_successor_cell

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
        "x": 15200,
        "y": 0,
        "z": 3900,
        "facing": 0,
        "hp": 96,
        "cam_id": 0,
        "character_id": 1,
        "in_control": True,
        "inventory": ["shotgun"],
        "dead": False,
        "step": 1,
    }
    s.update(kw)
    return s


def test_route_cp46_enter_is_room_only_kills_are_capture() -> None:
    cp = next(row for row in _ROUTE if row["checkpoint_id"] == "upper_hall_enter_203")
    assert cp["success_condition"] == {"type": "room_enter", "room_id": "203"}


def test_upper_hall_planner_pays_enter_without_204_kills() -> None:
    planner = _planner("upper_hall_enter_203")
    progress = ProgressTracker()
    prev = _state("204")
    state = _state("203")
    assert planner.advance_if_success(state, progress=progress, prev_state=prev)


def test_capture_rejects_upper_hall_without_204_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    bridge = MagicMock()
    bridge.save_savestate.side_effect = lambda path: Path(path).write_bytes(b"state")
    planner = _planner("upper_hall_enter_203")
    planner._index = int(_ROUTE_INDEX["upper_hall_enter_203"]) + 1
    progress = ProgressTracker()
    progress.leg_kills_by_room["204"] = 1
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
        _step_count=400,
        _read_state=lambda track_items=False: _state("203"),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )
    assert (
        capture_successor_cell(
            env,
            _state("203"),
            {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
        )
        is None
    )
    progress.leg_kills_by_room["204"] = 2
    proposal = capture_successor_cell(
        env,
        _state("203"),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert proposal is not None
    assert proposal["checkpoint_id"] == "upper_hall_enter_203"
    assert proposal["room_id"] == "203"
