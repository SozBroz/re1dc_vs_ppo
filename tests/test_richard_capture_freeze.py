"""Regression: deferred CP capture must not skip Richard cutscene → forced return."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import compute_reward
from re1_rl.room_graph import RoomGraph

YAWN_ROUTE = PROJECT_ROOT / "data" / "yawn_checkpoint_route.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"


def _planner(start_id: str) -> WaypointPlanner:
    import json

    route = json.loads(YAWN_ROUTE.read_text(encoding="utf-8"))
    idx = next(i for i, r in enumerate(route) if r["checkpoint_id"] == start_id)
    return WaypointPlanner(
        YAWN_ROUTE,
        route_steps=list(range(1, len(route) + 1)),
        start_index=idx,
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


def test_freeze_blocks_forced_return_auto_advance() -> None:
    """After cutscene succeeds into 204, freeze must stop room_enter(204) skip."""
    from re1_rl.richard_cutscene_checkpoint import RICHARD_CUTSCENE_KEY

    g = RoomGraph(DOORS)
    planner = _planner("richard_cutscene_20D")
    progress = ProgressTracker(leg_span=1)
    # Cutscene dump mints ledger + advances once.
    progress.observe_cutscene(RICHARD_CUTSCENE_KEY)
    _, bd = compute_reward(
        _state("20D"),
        _state("204"),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] > 0.0
    assert planner.current_objective()["checkpoint_id"] == "richard_forced_return_204"
    completed_at_freeze = int(planner.waypoint_index) - 1

    # Simulate env freeze: further reward ticks must not advance.
    progress.checkpoint_freeze_pending = True
    _, bd2 = compute_reward(
        _state("204"),
        _state("204"),
        planner,
        progress=progress,
        graph=g,
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd2["checkpoint_success"] == 0.0
    assert planner.current_objective()["checkpoint_id"] == "richard_forced_return_204"
    assert int(planner.waypoint_index) - 1 == completed_at_freeze
