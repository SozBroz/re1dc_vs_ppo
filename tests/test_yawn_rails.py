"""Offline contract tests for the Yawn one-leg rails curriculum."""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from re1_rl.obs_encoder import GOAL_FIELDS, ObsEncoder
from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import CHECKPOINT_REWARD, compute_reward
from re1_rl.room_graph import RoomGraph, load_valid_rooms
from re1_rl.yawn_rails import (
    capture_successor_cell,
    sample_one_leg_options,
    validate_route,
)

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "data" / "yawn_checkpoint_route.json"
ROOMS = ROOT / "data" / "rooms.json"
DOORS = ROOT / "data" / "doors_empirical.json"
DOORS_RDT = ROOT / "data" / "doors_rdt.json"
GOAL_IDX = {name: i for i, (name, _) in enumerate(GOAL_FIELDS)}


def _graph() -> RoomGraph:
    return RoomGraph(
        DOORS,
        DOORS_RDT,
        valid_rooms=load_valid_rooms(ROOMS),
    )


def _planner(start_index: int = 0) -> WaypointPlanner:
    return WaypointPlanner(
        ROUTE,
        route_steps=list(range(1, 43)),
        start_index=start_index,
    )


def _state(room: str, *, inventory=(), new_items=(), x=0, z=0) -> dict:
    return {
        "room_id": room,
        "inventory": list(inventory),
        "new_items": list(new_items),
        "x": x,
        "z": z,
        "facing": 0,
        "hp": 96,
        "in_control": True,
        "dead": False,
    }


def test_route_is_legal_and_excludes_rejected_objectives() -> None:
    route = json.loads(ROUTE.read_text(encoding="utf-8"))
    curriculum = json.loads(
        (ROOT / "curriculum/yawn_rails_one_leg.json").read_text(encoding="utf-8")
    )
    assert validate_route(route, graph=_graph()) == []
    assert curriculum["max_steps"] == 2700  # 6 min at 8 frames/step and 60fps.
    text = json.dumps(route).lower()
    assert '"205"' not in text
    assert "serum" not in text
    assert any(cp["room_id"] == "20D" and "handgun_bullets" in cp["items_gained"] for cp in route)
    for checkpoint in route:
        condition_text = json.dumps(checkpoint["success_condition"])
        for item in checkpoint["items_gained"]:
            assert f'"item": "{item}"' in condition_text


def test_zero_coordinate_rdt_rows_are_not_walkable_edges() -> None:
    graph = _graph()
    assert graph.get_exit("20E", "100") is None
    assert graph.get_exit("20E", "210") is not None


def test_checkpoint_requires_this_leg_pickup_and_pays_terminal_1_2() -> None:
    planner = _planner()
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    held_only = _state("105", inventory=["emblem"])
    _, miss = compute_reward(
        _state("105"),
        held_only,
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert miss["checkpoint_success"] == 0.0

    acquired = _state("105", inventory=["emblem"], new_items=["emblem"])
    reward, hit = compute_reward(
        _state("105"),
        acquired,
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert hit["checkpoint_success"] == pytest.approx(CHECKPOINT_REWARD)
    assert progress.checkpoint_success
    assert max(v for k, v in hit.items() if k != "checkpoint_success") < CHECKPOINT_REWARD
    assert reward > CHECKPOINT_REWARD - 0.01


def test_goal_encodes_selected_one_leg_checkpoint() -> None:
    graph = _graph()
    encoder = ObsEncoder(ROOMS, graph, curriculum_stage_index=1)
    planner = _planner(start_index=40)
    state = _state("20D", x=1000, z=1000)
    goal = encoder.encode_goal(state, planner)
    assert planner.next_waypoint_room() == "20E"
    assert goal[GOAL_IDX["goal_room_index"]] == encoder._room_idx_norm("20E")
    assert goal[GOAL_IDX["doors_available"]] == 1.0
    assert goal[GOAL_IDX["waypoints_remaining"]] == pytest.approx(2 / 42)


def test_route_cell_sampling_is_seed_deterministic_and_never_archive(tmp_path: Path) -> None:
    (tmp_path / "states/cp0").mkdir(parents=True)
    (tmp_path / "states/cp0/cell.State").write_bytes(b"state")
    (tmp_path / "states/cp0/cell.sidecar.json").write_text("{}", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [{
            "checkpoint_index": 0,
            "state_path": "states/cp0/cell.State",
            "sidecar_path": "states/cp0/cell.sidecar.json",
        }],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {"route_id": "test", "cells_manifest": "manifest.json"}
    a = sample_one_leg_options(tmp_path, stage, rng=random.Random(7))
    b = sample_one_leg_options(tmp_path, stage, rng=random.Random(7))
    assert a == b
    assert a["reset_source"] in {"route_initial", "route_cell"}
    assert a["reset_source"] not in {"pb", "archive"}


def test_checkpoint_success_captures_successor_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = MagicMock()
    bridge.save_savestate.side_effect = (
        lambda path: Path(path).write_bytes(b"state")
    )
    planner = _planner()
    planner._index = 1
    env = SimpleNamespace(
        project_root=tmp_path,
        _stage={
            "mode": "yawn_rails",
            "cells_manifest": "states/yawn_rails/manifest.json",
            "route_id": "test",
        },
        _planner=planner,
        bridge=bridge,
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )

    proposal = capture_successor_cell(
        env,
        _state("105"),
        {"checkpoint_success": CHECKPOINT_REWARD},
    )

    captured = tmp_path / "states/yawn_rails/cells/cp00/cell.State"
    assert proposal is not None
    assert proposal["checkpoint_index"] == 0
    bridge.save_savestate.assert_called_once_with(str(captured))
    manifest = json.loads(
        (tmp_path / "states/yawn_rails/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["cells"][0]["checkpoint_index"] == 0
    assert manifest["cells"][0]["checkpoint_id"] == "emblem_105"


def test_11b_almanac_has_chemical_but_not_square_crank() -> None:
    room_items = json.loads((ROOT / "data/room_items.json").read_text(encoding="utf-8"))
    names = {row["name"] for row in room_items["11B"]["items"]}
    assert "chemical" in names
    assert "square_crank" not in names
