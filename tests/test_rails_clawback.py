"""Rails-mode pickup/put-back symmetry for weapons and key items."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    GOLD_EMBLEM_RETURN_PENALTY,
    KEY_ITEM_PICKUP_BONUS,
    KEY_ITEM_RETURN_PENALTY,
    NEW_WEAPON_PICKUP_BONUS,
    RAILS_AUX_POSITIVE_SCALE,
    SHOTGUN_RETURN_PENALTY,
    compute_reward,
)
from re1_rl.room_graph import RoomGraph

ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
DOORS = PROJECT_ROOT / "data" / "doors_empirical.json"


def make_state(room: str, step: int = 1, **kw):
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
        "step": step,
    }
    s.update(kw)
    return s


def _reward(prev, state, *, rails: bool, progress: ProgressTracker | None = None):
    graph = RoomGraph(DOORS)
    planner = WaypointPlanner(ROUTE, route_steps=[])
    progress = progress or ProgressTracker()
    _, bd = compute_reward(
        prev,
        state,
        planner,
        progress=progress,
        graph=graph,
        rails_mode=rails,
        return_breakdown=True,
    )
    return bd


def test_rails_shotgun_take_return_is_net_zero():
    progress = ProgressTracker()
    empty = make_state("115", inventory=[])
    held = make_state("115", step=2, inventory=["shotgun"], new_items=["shotgun"])
    returned = make_state("115", step=3, inventory=[])

    pickup = _reward(empty, held, rails=True, progress=progress)
    ret = _reward(held, returned, rails=True, progress=progress)

    scaled_weapon = NEW_WEAPON_PICKUP_BONUS * RAILS_AUX_POSITIVE_SCALE
    assert pickup["new_weapon"] == scaled_weapon
    assert ret["shotgun_return"] == SHOTGUN_RETURN_PENALTY * RAILS_AUX_POSITIVE_SCALE
    assert pickup["new_weapon"] + ret["shotgun_return"] == 0.0


def test_exploration_shotgun_take_return_still_full_magnitude():
    progress = ProgressTracker()
    empty = make_state("115", inventory=[])
    held = make_state("115", step=2, inventory=["shotgun"], new_items=["shotgun"])
    returned = make_state("115", step=3, inventory=[])

    pickup = _reward(empty, held, rails=False, progress=progress)
    ret = _reward(held, returned, rails=False, progress=progress)

    assert pickup["new_weapon"] == NEW_WEAPON_PICKUP_BONUS
    assert ret["shotgun_return"] == SHOTGUN_RETURN_PENALTY
    assert pickup["new_weapon"] + ret["shotgun_return"] == 0.0


def test_rails_key_item_take_return_is_net_zero():
    progress = ProgressTracker()
    empty = make_state("105", inventory=[])
    held = make_state("105", step=2, inventory=["emblem"], new_items=["emblem"])
    returned = make_state("105", step=3, inventory=[])

    pickup = _reward(empty, held, rails=True, progress=progress)
    ret = _reward(held, returned, rails=True, progress=progress)

    scaled_key = KEY_ITEM_PICKUP_BONUS * RAILS_AUX_POSITIVE_SCALE
    assert pickup["key_item"] == scaled_key
    assert ret["key_item_return"] == KEY_ITEM_RETURN_PENALTY * RAILS_AUX_POSITIVE_SCALE
    assert pickup["key_item"] + ret["key_item_return"] == 0.0
    assert "emblem" not in progress.key_items_rewarded


def test_key_item_return_skipped_on_story_use():
    progress = ProgressTracker()
    progress.key_items_rewarded.add("emblem")
    prev = make_state("105", inventory=["emblem"])
    used = make_state("105", step=2, inventory=[], story_use_success="emblem@105_fireplace")

    bd = _reward(prev, used, rails=True, progress=progress)
    assert bd["key_item_return"] == 0.0


def test_key_item_return_skipped_in_box_room():
    progress = ProgressTracker()
    progress.key_items_rewarded.add("emblem")
    prev = make_state("100", inventory=["emblem"])
    boxed = make_state("100", step=2, inventory=[])

    bd = _reward(prev, boxed, rails=True, progress=progress)
    assert bd["key_item_return"] == 0.0


def test_rails_gold_emblem_return_scales_with_pickup():
    progress = ProgressTracker()
    prev = make_state("10F", inventory=["gold_emblem"])
    ret = make_state("10F", step=2, inventory=[], gold_emblem_return=True)

    bd = _reward(prev, ret, rails=True, progress=progress)
    assert bd["gold_emblem_return"] == GOLD_EMBLEM_RETURN_PENALTY * RAILS_AUX_POSITIVE_SCALE
