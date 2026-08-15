"""cp19 (L-passage ammo) requires killing both hallway dogs in 108."""

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
        "x": 3400,
        "y": 0,
        "z": 4000,
        "facing": 0,
        "hp": 96,
        "cam_id": 0,
        "character_id": 1,
        "in_control": True,
        "inventory": ["handgun_bullets"],
        "dead": False,
        "step": 1,
    }
    s.update(kw)
    return s


def test_route_cp19_bundles_ammo_with_two_108_kills() -> None:
    cp = next(row for row in _ROUTE if row["checkpoint_id"] == "ammo_108")
    assert cp["success_condition"] == {
        "type": "all_of",
        "conditions": [
            {"type": "acquired_item", "item": "handgun_bullets"},
            {"type": "leg_kills_in_room", "room_id": "108", "min_kills": 2},
        ],
    }


def test_ammo_checkpoint_blocked_without_both_kills() -> None:
    planner = _planner("ammo_108")
    progress = ProgressTracker()
    progress.note_leg_acquired("handgun_bullets")
    state = _state("108")
    assert not planner.advance_if_success(state, progress=progress)
    progress.note_leg_kills("108", 1)
    assert not planner.advance_if_success(state, progress=progress)
    progress.note_leg_kills("108", 1)
    assert planner.advance_if_success(state, progress=progress)


def test_capture_rejects_ammo_108_without_two_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    bridge = MagicMock()
    bridge.save_savestate.side_effect = lambda path: Path(path).write_bytes(b"state")
    planner = _planner("ammo_108")
    planner._index = int(_ROUTE_INDEX["ammo_108"]) + 1
    progress = ProgressTracker()
    progress.leg_kills_by_room["108"] = 1
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
        _read_state=lambda track_items=False: _state("108"),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )
    assert (
        capture_successor_cell(
            env,
            _state("108"),
            {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
        )
        is None
    )
    progress.leg_kills_by_room["108"] = 2
    proposal = capture_successor_cell(
        env,
        _state("108"),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert proposal is not None
    assert proposal["checkpoint_id"] == "ammo_108"
    assert proposal["room_id"] == "108"
