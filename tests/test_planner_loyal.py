"""Unit tests for planner-loyal queue + encoding."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from re1_rl.env import RE1Env
from re1_rl.gallery_puzzle import (
    GALLERY_FINAL_SWITCH_TARGET,
    GALLERY_STEP_VALUES,
    GALLERY_TARGETS,
    GALLERY_WRONG_PORTRAIT_PENALTY,
    encode_gallery_hint,
)
from re1_rl.planner import WaypointPlanner
from re1_rl.planner_loyal import (
    HEAL_USE_TAX_LIGHT,
    PLANNER_DIVERT_PENALTY,
    PLANNER_MAX_STEPS,
    PLANNER_QUEUE_DIM,
    PLANNER_STEP_SUCCESS_REWARD,
    PlannerLoyalQueue,
    apply_planner_loyal_obs,
    encode_planner_queue,
    _planner_step_target_xz,
    load_chunk,
    planner_loyal_enabled,
    prune_route_admin_goal,
    read_queue_current,
    validate_planner_loyal_chunk,
)
from re1_rl.room_graph import RoomGraph
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    CHECKPOINT_MAX_STEPS_EXTENSION,
    MAIN_HALL_BEFORE_KENNETH_PENALTY,
    STEP_PENALTY,
    WEAPON_RELOAD_REWARD,
    compute_reward,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"


def _planner() -> WaypointPlanner:
    return WaypointPlanner(ROUTE, waypoints=["105"])


def _reward(prev, cur, queue, *, progress=None, box_opened=False):
    return compute_reward(
        prev,
        cur,
        _planner(),
        progress=progress,
        planner_loyal_queue=queue,
        box_opened=box_opened,
        return_breakdown=True,
    )


def test_load_cp05_chunk_has_emblem_swap_and_clips():
    chunk = load_chunk()
    steps = chunk["steps"]
    pickups = [s.get("pickup_id") for s in steps]
    sites = [s.get("site_id") for s in steps]
    assert "104:handgun_bullets:1" in pickups
    assert "104:handgun_bullets:2" in pickups
    # Tip already holds wooden emblem — never re-acquire in this chunk.
    assert not any(
        str(p or "").startswith("105:emblem") for p in pickups
    )
    assert "emblem@10F_alcove" in sites
    assert any(
        str(p or "").startswith("105:shield_key") for p in pickups
    )
    assert any(
        str(p or "").startswith("118:chemical") for p in pickups
    )
    assert any(s.get("op") == "use_box" for s in steps)
    assert any(
        str(s.get("pickup_id") or "").startswith("10C:armor_key") for s in steps
    )
    assert any(s.get("edge_id") == "101->100" for s in steps)
    assert any(s.get("op") == "use_box" and s.get("room_id") == "100" for s in steps)
    assert any(s.get("edge_id") == "204->205" for s in steps)
    sun_i = next(i for i, s in enumerate(steps) if s.get("beat_id") == "sun_crest")
    assert [step.get("beat_id") for step in steps[sun_i - 3 : sun_i + 1]] == [
        "armor_room_enter",
        "armor_vent_door",
        "armor_vent_far",
        "sun_crest",
    ]
    assert steps[sun_i]["pickup_id"] == "205:sun_crest:1"
    assert steps[sun_i + 1]["edge_id"] == "205->204"
    assert steps[sun_i + 3]["beat_id"] == "richard_bleedout"
    assert steps[sun_i + 3]["site_id"] == "20D:richard"
    assert steps[sun_i + 3].get("capture") is False
    # Richard dump is already in 204 — no 20D→204 before the right-stairs hop.
    assert not any(
        s.get("edge_id") == "20D->204" for s in steps[sun_i : sun_i + 5]
    )
    assert steps[sun_i + 4]["edge_id"] == "204->207"
    assert any(s.get("beat_id") == "place_sun_crest" for s in steps)
    assert any(s.get("beat_id") == "dining_2f_enter" for s in steps)
    assert any(s.get("beat_id") == "push_statue_2f" for s in steps)
    statue_i = next(i for i, s in enumerate(steps) if s.get("beat_id") == "push_statue_2f")
    assert steps[statue_i]["n"] == 94
    assert steps[statue_i]["site_id"] == "dining_statue_knocked"
    assert steps[statue_i + 1]["edge_id"] == "202->203"
    assert steps[statue_i + 4]["pickup_id"] == "105:blue_jewel:1"
    assert steps[statue_i + 5]["edge_id"] == "105->106"
    assert steps[statue_i + 10]["edge_id"] == "101->103"
    assert steps[statue_i + 11]["edge_id"] == "103->10D"
    assert steps[statue_i + 12]["site_id"] == "blue_jewel@10D_tiger_eye"
    assert steps[statue_i + 13]["pickup_id"] == "10D:wind_crest:1"
    assert steps[statue_i + 13]["beat_id"] == "wind_crest"
    # Resource-first place_wind tail (pl110 Muse): 10E loot, 103->104 unlock,
    # 111 clips, wardrobe 112 greens, art→11A.
    assert any(s.get("beat_id") == "place_wind_crest" for s in steps)
    assert any(s.get("beat_id") == "place_moon_crest" for s in steps)
    assert any(s.get("beat_id") == "shed_push_stepladder" for s in steps)
    assert any(s.get("beat_id") == "square_crank" for s in steps)
    assert not any(s.get("beat_id") == "plant_42" for s in steps)
    assert not any(s.get("beat_id") == "helmet_key" for s in steps)
    shed_i = next(i for i, s in enumerate(steps) if s.get("beat_id") == "shed_push_stepladder")
    assert steps[shed_i]["op"] == "do_puzzle"
    assert steps[shed_i]["n"] == 196
    assert steps[shed_i + 1]["beat_id"] == "square_crank"
    assert steps[shed_i + 1]["pickup_id"] == "11B:square_crank:1"
    assert steps[-1]["beat_id"] == "square_crank"
    assert steps[-1]["n"] == 197
    assert steps[-1]["pickup_id"] == "11B:square_crank:1"
    assert chunk["end_anchor_beat_id"] == "square_crank"
    wind_i = next(i for i, s in enumerate(steps) if s.get("beat_id") == "wind_crest")
    assert steps[wind_i]["n"] == 107
    assert steps[wind_i + 1]["edge_id"] == "10D->103"
    assert steps[wind_i + 2]["edge_id"] == "103->10E"
    assert steps[wind_i + 3]["pickup_id"] == "10E:handgun_bullets:1"
    assert steps[wind_i + 4]["pickup_id"] == "10E:shotgun_shells:2"
    assert not any(s.get("edge_id") == "103->104" for s in steps[: wind_i + 1])
    assert any(s.get("edge_id") == "103->104" for s in steps[wind_i + 1 :])
    assert any(s.get("pickup_id") == "111:handgun_bullets:1" for s in steps[wind_i:])
    assert any(s.get("pickup_id") == "111:shotgun_shells:2" for s in steps[wind_i:])
    shells_i = next(
        i for i, s in enumerate(steps) if s.get("pickup_id") == "111:shotgun_shells:2"
    )
    assert steps[shells_i + 1]["edge_id"] == "111->112"
    assert steps[shells_i + 2]["pickup_id"] == "112:green_herb:1"
    assert steps[shells_i + 3]["pickup_id"] == "112:green_herb:2"
    assert steps[shells_i + 4]["edge_id"] == "112->111"
    assert steps[shells_i + 5]["edge_id"] == "111->106"
    assert not any(
        s.get("pickup_id", "").startswith("112:ink") for s in steps
    )  # skip ink ribbons
    assert not any(s.get("edge_id") == "105->104" for s in steps[statue_i:wind_i + 1])
    assert not any(s.get("edge_id") == "104->103" for s in steps)
    assert chunk["leave_100"]["next_beat_this_loadout_is_for"] == "richard_bleedout"
    assert chunk["leave_100"]["held_on_exit"][2]["item"] == "armor_key"
    assert (
        sum(
            int(r.get("qty") or 0)
            for r in chunk["leave_100"]["held_on_exit"]
            if r.get("item") == "handgun_bullets"
        )
        == 15
    )
    assert chunk["leave_100"]["withdraw_from_box"] == [
        {"item": "handgun_bullets", "qty": 15, "why": "combat kit; one clip is enough"}
    ]
    assert not any(
        str(r.get("item") or "") in {
            "green_herb",
            "red_herb",
            "blue_herb",
            "mixed_herbs_ggg",
            "mixed_herbs_gr",
            "first_aid_spray_alt",
        }
        for r in chunk["leave_100"]["held_on_exit"]
    )
    assert any(s.get("edge_id") == "116->115" for s in steps)
    assert any(s.get("edge_id") == "115->109" for s in steps)
    assert any(s.get("edge_id") == "10A->117" for s in steps)
    assert not any(s.get("edge_id") == "116->106" for s in steps)
    assert any(s.get("edge_id") == "103->10C" for s in steps)
    portraits = [
        s.get("beat_id") for s in steps if str(s.get("beat_id") or "").startswith("gallery_portrait_")
    ]
    assert portraits == [f"gallery_portrait_{i}" for i in range(1, 7)]
    assert any(s.get("site_id") == "gallery_end_of_life" for s in steps)
    crest_i = next(i for i, s in enumerate(steps) if str(s.get("pickup_id") or "").startswith("117:star_crest"))
    old_man = next(i for i, s in enumerate(steps) if s.get("beat_id") == "gallery_portrait_6")
    assert old_man < crest_i
    assert steps[old_man + 1]["site_id"] == "gallery_end_of_life"
    assert steps[old_man + 2]["pickup_id"].startswith("117:star_crest")
    assert steps[crest_i + 1]["edge_id"] == "117->10A"
    assert steps[crest_i + 2]["edge_id"] == "10A->11A"
    assert steps[crest_i + 3]["site_id"] == "star_crest@11A_crest_slot"
    pump_i = next(i for i, s in enumerate(steps) if s.get("site_id") == "chemical@10C_greenhouse_pump")
    armor_i = next(i for i, s in enumerate(steps) if str(s.get("pickup_id") or "").startswith("10C:armor_key"))
    herb_ids = [
        s.get("pickup_id")
        for s in steps[armor_i + 1 : armor_i + 5]
    ]
    assert herb_ids == [
        "10C:red_herb:3a",
        "10C:green_herb:2a",
        "10C:red_herb:3b",
        "10C:green_herb:2b",
    ]
    assert pump_i < armor_i
    assert steps[pump_i - 1]["edge_id"] == "103->10C"
    assert any(s.get("edge_id") == "106->107" for s in steps)
    assert "108:handgun_bullets:1" in pickups
    enter_108 = next(i for i, s in enumerate(steps) if s.get("edge_id") == "107->108")
    assert steps[enter_108 + 1]["pickup_id"] == "108:handgun_bullets:1"


def test_read_queue_current_is_step_dict_not_callable():
    q = PlannerLoyalQueue()
    assert not callable(q.current)
    cur = read_queue_current(q)
    assert isinstance(cur, dict)
    assert cur.get("edge_id") == "106->105"


def test_opening_chunk_emblem_acquire_is_first_step():
    path = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=path)
    assert q.current["pickup_id"] == "105:emblem:1"
    # pl00 is the fresh-start tip; emblem is the first minted slot.
    assert q.current["slot_index"] == 1
    assert [s["slot_index"] for s in q._steps] == [1, 2, 3, 4, 5, 6]
    prev = {"room_id": "105", "inventory_slots": [("beretta", 15), ("knife", 0)]}
    q.note_start_inventory(prev)
    result = q.evaluate_transition(
        prev_state=prev,
        state={
            "room_id": "105",
            "inventory_slots": [("beretta", 15), ("knife", 0), ("emblem", 1)],
            "new_items": ["emblem"],
        },
    )
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.current["edge_id"] == "105->104"


def test_queue_pops_on_correct_traverse():
    q = PlannerLoyalQueue()
    assert q.current["edge_id"] == "106->105"
    prev = {"room_id": "106", "inventory_slots": []}
    cur = {"room_id": "105", "inventory_slots": []}
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is True
    assert q.current["edge_id"] == "105->104"


def test_queue_divert_on_wrong_room():
    q = PlannerLoyalQueue()
    prev = {"room_id": "106", "inventory_slots": []}
    cur = {"room_id": "107", "inventory_slots": []}
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is True


def test_ammo_on_traverse_into_tea_room_diverts():
    q = PlannerLoyalQueue()
    first = q.evaluate_transition(
        prev_state={"room_id": "106", "inventory_slots": [("beretta", 15)]},
        state={"room_id": "105", "inventory_slots": [("beretta", 15)]},
    )
    assert first["step_success"] is True
    assert q.current["edge_id"] == "105->104"
    result = q.evaluate_transition(
        prev_state={"room_id": "105", "inventory_slots": [("beretta", 15)]},
        state={
            "room_id": "104",
            "inventory_slots": [("beretta", 15), ("handgun_bullets", 15)],
            "new_items": ["handgun_bullets"],
        },
    )
    assert result["divert"] is True
    assert "handgun_bullets" in str(result["divert_reason"])
    assert result["step_success"] is False
    assert q.current["edge_id"] == "105->104"


def test_ammo_while_already_in_traverse_dest_diverts():
    q = PlannerLoyalQueue()
    q.seek(1)
    assert q.current["edge_id"] == "105->104"
    result = q.evaluate_transition(
        prev_state={"room_id": "104", "inventory_slots": [("beretta", 15)]},
        state={
            "room_id": "104",
            "inventory_slots": [("beretta", 15), ("handgun_bullets", 15)],
            "new_items": ["handgun_bullets"],
        },
    )
    assert result["divert"] is True
    assert "handgun_bullets" in str(result["divert_reason"])
    assert result["step_success"] is False


def test_clean_traverse_into_tea_room_still_completes():
    q = PlannerLoyalQueue()
    q.evaluate_transition(
        prev_state={"room_id": "106", "inventory_slots": [("beretta", 15)]},
        state={"room_id": "105", "inventory_slots": [("beretta", 15)]},
    )
    result = q.evaluate_transition(
        prev_state={"room_id": "105", "inventory_slots": [("beretta", 15)]},
        state={"room_id": "104", "inventory_slots": [("beretta", 15)]},
    )
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.current["op"] == "acquire"
    assert q.current["pickup_id"] == "104:handgun_bullets:1"


def test_ink_ribbon_pickup_diverts_on_traverse():
    q = PlannerLoyalQueue()
    prev = {"room_id": "106", "inventory_slots": []}
    cur = {
        "room_id": "106",
        "inventory_slots": [("ink_ribbon", 1)],
        "new_items": ["ink_ribbon"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is True
    assert "unplanned_pickup" in str(result["divert_reason"])
    assert result["step_success"] is False
    assert q.current["edge_id"] == "106->105"


def test_ink_ribbon_pickup_diverts_during_use_box():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test",
            "steps": [
                {"n": 1, "op": "use_box", "room_id": "118"},
                {"n": 2, "op": "traverse", "edge_id": "118->10B"},
            ],
        }
    )
    q.note_start_inventory(
        {"room_id": "118", "inventory_slots": [("knife", 1), ("chemical", 1)]}
    )
    result = q.evaluate_transition(
        prev_state={
            "room_id": "118",
            "inventory_slots": [("knife", 1), ("chemical", 1)],
        },
        state={
            "room_id": "118",
            "inventory_slots": [("knife", 1), ("chemical", 1), ("ink_ribbon", 2)],
            "new_items": ["ink_ribbon"],
        },
    )
    assert result["divert"] is True
    assert result["divert_reason"] == "unplanned_pickup:['ink_ribbon']"


def test_ink_ribbon_pickup_diverts_during_unique_acquire():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test",
            "steps": [
                {"n": 1, "op": "acquire", "pickup_id": "118:chemical:1", "room_id": "118"},
                {"n": 2, "op": "traverse", "edge_id": "118->10B"},
            ],
        }
    )
    q.note_start_inventory({"room_id": "118", "inventory_slots": [("knife", 1)]})
    result = q.evaluate_transition(
        prev_state={"room_id": "118", "inventory_slots": [("knife", 1)]},
        state={
            "room_id": "118",
            "inventory_slots": [("knife", 1), ("chemical", 1), ("ink_ribbon", 2)],
            "new_items": ["chemical", "ink_ribbon"],
        },
    )
    assert result["divert"] is True
    assert result["divert_reason"] == "unplanned_pickup:['ink_ribbon']"
    assert result["step_success"] is False


def test_held_unplanned_ink_ribbon_diverts_without_rising_edge():
    q = PlannerLoyalQueue()
    q.note_start_inventory({"room_id": "106", "inventory_slots": [("knife", 1)]})
    slots = [("knife", 1), ("ink_ribbon", 2)]
    result = q.evaluate_transition(
        prev_state={"room_id": "106", "inventory_slots": slots},
        state={"room_id": "106", "inventory_slots": slots},
    )
    assert result["divert"] is True
    assert result["divert_reason"] == "unplanned_pickup:['ink_ribbon']"


def test_start_inventory_decode_hole_does_not_divert():
    q = PlannerLoyalQueue()
    q.note_start_inventory(
        {"room_id": "10B", "inventory_slots": [("beretta", 15), ("armor_key", 1)]}
    )
    result = q.evaluate_transition(
        prev_state={"room_id": "10B", "inventory_slots": []},
        state={
            "room_id": "10B",
            "inventory_slots": [("beretta", 15), ("armor_key", 1)],
        },
    )
    assert result["divert"] is False


def test_first_eval_room_flicker_into_start_does_not_divert():
    q = PlannerLoyalQueue()
    q.note_start_inventory({"room_id": "10F", "inventory_slots": []})
    result = q.evaluate_transition(
        prev_state={"room_id": "100", "inventory_slots": []},
        state={"room_id": "10F", "inventory_slots": []},
    )
    assert result["divert"] is False
    later = q.evaluate_transition(
        prev_state={"room_id": "10F", "inventory_slots": []},
        state={"room_id": "107", "inventory_slots": []},
    )
    assert later["divert"] is True


def test_already_held_beretta_qty_bump_does_not_divert():
    q = PlannerLoyalQueue()
    q.note_start_inventory(
        {"room_id": "204", "inventory_slots": [("beretta", 5), ("handgun_bullets", 45)]}
    )
    result = q.evaluate_transition(
        prev_state={
            "room_id": "204",
            "inventory_slots": [("beretta", 5), ("handgun_bullets", 45)],
        },
        state={
            "room_id": "204",
            "inventory_slots": [("beretta", 15), ("handgun_bullets", 45)],
            "new_items": ["beretta"],
        },
    )
    assert result["divert"] is False
    assert result["step_success"] is False


def _108_fire_states(chamber_before: int, chamber_after: int, **extra):
    prev = {
        "room_id": "108",
        "inventory_slots": [
            ("beretta", chamber_before),
            ("handgun_bullets", 30),
            ("knife", 0),
        ],
    }
    cur = {
        "room_id": "108",
        "inventory_slots": [
            ("beretta", chamber_after),
            ("handgun_bullets", 30),
            ("knife", 0),
        ],
    }
    cur.update(extra)
    return prev, cur


def test_firing_beretta_in_108_does_not_divert_or_mint_clip():
    """Spending a chambered round is a qty loss, not a floor-pile pickup.

    Historical hole: any inventory delta (including fire) looked like an
    unplanned beretta/ammo gain and killed the episode versus the PL bag.
    """
    q = PlannerLoyalQueue()
    idx = next(
        i
        for i, step in enumerate(q._steps)
        if step.get("pickup_id") == "108:handgun_bullets:1"
    )
    q.seek(idx)
    q.note_start_inventory(
        {"room_id": "108", "inventory_slots": [("beretta", 15), ("handgun_bullets", 30)]}
    )
    prev, cur = _108_fire_states(15, 14)
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is False
    assert q.current["pickup_id"] == "108:handgun_bullets:1"

    prev, cur = _108_fire_states(15, 14, new_items=["beretta"])
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is False


def test_firing_last_round_and_autoreload_in_108_do_not_divert():
    q = PlannerLoyalQueue()
    idx = next(
        i for i, step in enumerate(q._steps) if step.get("edge_id") == "108->109"
    )
    q.seek(idx)
    prev, cur = _108_fire_states(1, 0)
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is False

    prev = {
        "room_id": "108",
        "inventory_slots": [("beretta", 1), ("handgun_bullets", 30), ("knife", 0)],
    }
    cur = {
        "room_id": "108",
        "inventory_slots": [("beretta", 15), ("handgun_bullets", 16), ("knife", 0)],
        "new_items": ["beretta"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is False


def test_combine_reload_does_not_divert_on_traverse():
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "106",
        "inventory_slots": [("beretta", 0), ("handgun_bullets", 45)],
    }
    cur = {
        "room_id": "106",
        "inventory_slots": [("beretta", 15), ("handgun_bullets", 30)],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is False
    assert q.current["edge_id"] == "106->105"


def test_gl_ammo_load_retarget_does_not_divert():
    """Empty acid GL + explosive rounds → bazooka_explosive is a chamber load."""
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "210",
        "inventory_slots": [
            ("bazooka_acid", 0),
            ("explosive_rounds", 6),
            ("shield_key", 1),
        ],
    }
    q.note_start_inventory(prev)
    cur = {
        "room_id": "210",
        "inventory_slots": [
            ("bazooka_explosive", 6),
            ("shield_key", 1),
        ],
        "new_items": ["bazooka_explosive"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result.get("divert_reason") is None


def test_gl_reload_after_weapon_slot_vanishes_does_not_divert():
    """Firing the last GL round can clear the weapon slot; reload must not divert."""
    q = PlannerLoyalQueue()
    start = {
        "room_id": "210",
        "inventory_slots": [
            ("bazooka_acid", 1),
            ("acid_rounds", 6),
            ("explosive_rounds", 6),
            ("shield_key", 1),
        ],
    }
    q.note_start_inventory(start)
    # Empty chamber: weapon row gone (game packs the hole).
    prev = {
        "room_id": "210",
        "inventory_slots": [
            ("acid_rounds", 6),
            ("explosive_rounds", 6),
            ("shield_key", 1),
        ],
    }
    cur = {
        "room_id": "210",
        "inventory_slots": [
            ("bazooka_acid", 6),
            ("explosive_rounds", 6),
            ("shield_key", 1),
        ],
        "new_items": ["bazooka_acid"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result.get("divert_reason") is None


def test_gl_new_items_flicker_explosive_while_holding_acid_does_not_divert():
    """Live tips divert on new_items=['bazooka_explosive'] mid-Yawn — ignore."""
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "210",
        "inventory_slots": [
            ("bazooka_acid", 0),
            ("acid_rounds", 6),
            ("shield_key", 1),
        ],
    }
    q.note_start_inventory(prev)
    cur = {
        "room_id": "210",
        "inventory_slots": [
            ("bazooka_acid", 6),
            ("shield_key", 1),
        ],
        "new_items": ["bazooka_explosive"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result.get("divert_reason") is None


def test_gl_acid_reload_same_variant_does_not_divert():
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "210",
        "inventory_slots": [("bazooka_acid", 0), ("acid_rounds", 6)],
    }
    cur = {
        "room_id": "210",
        "inventory_slots": [("bazooka_acid", 6)],
        "new_items": ["bazooka_acid"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result.get("divert_reason") is None


def test_combine_reload_full_ammo_dump_packed_does_not_divert():
    """Emptying the reserve stack into the chamber must not look like a pickup.

    Live COMBINE often packs the emptied ammo hole left; qty-only chamber
    bumps are also covered by the already-held weapon exemption.
    """
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "106",
        "inventory_slots": [
            ("beretta", 0),
            ("handgun_bullets", 15),
            ("knife", 0),
            ("ink_ribbon", 1),
        ],
    }
    q.note_start_inventory(prev)
    cur = {
        "room_id": "106",
        "inventory_slots": [
            ("beretta", 15),
            ("knife", 0),
            ("ink_ribbon", 1),
        ],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result.get("divert_reason") is None
    assert result["step_success"] is False


def test_combine_herb_mix_does_not_divert_on_traverse():
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "106",
        "inventory_slots": [("green_herb", 1), ("green_herb", 1)],
    }
    cur = {
        "room_id": "106",
        "inventory_slots": [("mixed_herbs_gg", 1), ("", 0)],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is False


def test_combine_herb_mix_packed_slots_does_not_divert():
    """Live COMBINE often packs remaining items left over the empty hole."""
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "100",
        "inventory_slots": [
            ("green_herb", 1),
            ("red_herb", 1),
            ("beretta", 15),
            ("armor_key", 1),
            ("", 0),
            ("", 0),
            ("", 0),
            ("", 0),
        ],
    }
    cur = {
        "room_id": "100",
        "inventory_slots": [
            ("mixed_herbs_gr", 1),
            ("beretta", 15),
            ("armor_key", 1),
            ("", 0),
            ("", 0),
            ("", 0),
            ("", 0),
            ("", 0),
        ],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result.get("divert_reason") is None


def test_shotgun_after_unique_acquire_does_not_divert():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test",
            "steps": [
                {"n": 1, "op": "acquire", "pickup_id": "116:shotgun:1", "room_id": "116"},
                {"n": 2, "op": "traverse", "edge_id": "116->115"},
            ],
        }
    )
    q.note_start_inventory({"room_id": "116", "inventory_slots": [("knife", 1)]})
    first = q.evaluate_transition(
        prev_state={"room_id": "116", "inventory_slots": [("knife", 1)]},
        state={
            "room_id": "116",
            "inventory_slots": [("knife", 1), ("shotgun", 1)],
            "new_items": ["shotgun"],
        },
    )
    assert first["step_success"] is True
    assert first["divert"] is False
    assert q.current["edge_id"] == "116->115"
    leftover = q.evaluate_transition(
        prev_state={
            "room_id": "116",
            "inventory_slots": [("knife", 1), ("shotgun", 1)],
        },
        state={
            "room_id": "116",
            "inventory_slots": [("knife", 1), ("shotgun", 1)],
            "new_items": ["shotgun"],
        },
    )
    assert leftover["divert"] is False
    assert leftover["step_success"] is False


def test_cutscene_event_grant_does_not_divert_on_traverse():
    q = PlannerLoyalQueue()
    q.current["edge_id"] = "203->202"
    q.current["op"] = "traverse"
    prev = {"room_id": "203", "inventory_slots": [("knife", 1)]}
    cur = {
        "room_id": "203",
        "inventory_slots": [("knife", 1), ("acid_rounds", 6)],
        "new_items": ["acid_rounds"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is False
    assert result["divert_reason"] is None


def test_yawn_attic_shells_event_grant_does_not_divert_on_boss():
    """yawn_moon_210 loot: 210 shotgun_shells are event-gated, not a rogue pile."""
    q = PlannerLoyalQueue()
    idx = next(
        i
        for i, step in enumerate(q._steps)
        if step.get("op") == "boss" and step.get("beat_id") == "yawn_1"
    )
    q.seek(idx)
    assert q.current is not None
    assert q.current["op"] == "boss"
    prev = {
        "room_id": "210",
        "inventory_slots": [("bazooka_acid", 3), ("shield_key", 1)],
    }
    cur = {
        "room_id": "210",
        "inventory_slots": [
            ("bazooka_acid", 3),
            ("shield_key", 1),
            ("shotgun_shells", 7),
        ],
        "new_items": ["shotgun_shells"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result.get("divert_reason") is None


def test_yawn_intro_completes_on_cutscene_confirm():
    q = PlannerLoyalQueue()
    idx = next(
        i
        for i, step in enumerate(q._steps)
        if step.get("beat_id") == "yawn_intro"
    )
    q.seek(idx)
    prev = {"room_id": "210", "inventory_slots": [("shield_key", 1)]}
    cur = {
        "room_id": "210",
        "inventory_slots": [("shield_key", 1)],
        "yawn_cutscene_confirmed": True,
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.current is not None
    assert q.current.get("beat_id") == "yawn_1"
    assert q.pending_capture_indices == []


def test_yawn_intro_completes_on_combat_presence():
    q = PlannerLoyalQueue()
    idx = next(
        i
        for i, step in enumerate(q._steps)
        if step.get("beat_id") == "yawn_intro"
    )
    q.seek(idx)
    yawn = {
        "slot": 0,
        "type_id": 0x0F,
        "hp": 120,
        "hp_raw": 3050,
        "yawn_translated": True,
        "active_byte": 1,
        "in_room": 1,
        "alive": 1,
    }
    result = q.evaluate_transition(
        prev_state={"room_id": "210", "inventory_slots": [], "enemies": []},
        state={"room_id": "210", "inventory_slots": [], "enemies": [yawn]},
    )
    assert result["step_success"] is True
    assert q.current.get("beat_id") == "yawn_1"


def test_yawn_boss_midhop_then_shells_acquire_mints():
    """Intro+boss are capture:false; first mint is the shells acquire hop."""
    q = PlannerLoyalQueue()
    intro_i = next(
        i for i, step in enumerate(q._steps) if step.get("beat_id") == "yawn_intro"
    )
    assert q._steps[intro_i].get("capture") is False
    boss_i = next(
        i
        for i, step in enumerate(q._steps)
        if step.get("op") == "boss" and step.get("beat_id") == "yawn_1"
    )
    assert q._steps[boss_i].get("capture") is False
    q.seek(boss_i)
    progress = ProgressTracker()
    progress.yawn_retreated = True
    boss = q.evaluate_transition(
        prev_state={"room_id": "210", "inventory_slots": [("shield_key", 1)]},
        state={"room_id": "210", "inventory_slots": [("shield_key", 1)]},
        progress=progress,
    )
    assert boss["step_success"] is True
    assert q.pending_capture_indices == []
    assert q.current.get("pickup_id") == "210:shotgun_shells:2"
    shells = q.evaluate_transition(
        prev_state={"room_id": "210", "inventory_slots": [("shield_key", 1)]},
        state={
            "room_id": "210",
            "inventory_slots": [("shield_key", 1), ("shotgun_shells", 7)],
            "new_items": ["shotgun_shells"],
        },
        progress=progress,
    )
    assert shells["step_success"] is True
    assert len(q.pending_capture_indices) == 1
    assert q.current.get("pickup_id") == "210:moon_crest:1"


def test_yawn_leave_after_retreat_completes_boss_not_divert():
    q = PlannerLoyalQueue()
    idx = next(
        i
        for i, step in enumerate(q._steps)
        if step.get("op") == "boss" and step.get("beat_id") == "yawn_1"
    )
    q.seek(idx)
    progress = ProgressTracker()
    progress.yawn_retreated = True
    result = q.evaluate_transition(
        prev_state={"room_id": "210", "inventory_slots": [("shield_key", 1)]},
        state={"room_id": "20E", "inventory_slots": [("shield_key", 1)]},
        progress=progress,
    )
    assert result["divert"] is False
    assert result["step_success"] is True


def _seek_yawn_stairs_leave(q: PlannerLoyalQueue) -> None:
    idx = next(
        i
        for i, step in enumerate(q._steps)
        if step.get("edge_id") == "20E->20D"
    )
    q.seek(idx)


def test_yawn_stairs_leave_normal_20d_with_crest():
    q = PlannerLoyalQueue()
    _seek_yawn_stairs_leave(q)
    held = [("shield_key", 1), ("moon_crest", 1), ("shotgun_shells", 7)]
    result = q.evaluate_transition(
        prev_state={"room_id": "20E", "inventory_slots": held},
        state={"room_id": "20D", "inventory_slots": held},
    )
    assert result["divert"] is False
    assert result["step_success"] is True
    assert len(q.pending_capture_indices) == 1
    assert q.current.get("edge_id") == "20D->204"


def test_yawn_stairs_leave_bite_warp_100_with_crest():
    q = PlannerLoyalQueue()
    _seek_yawn_stairs_leave(q)
    held = [("shield_key", 1), ("moon_crest", 1), ("shotgun_shells", 7)]
    result = q.evaluate_transition(
        prev_state={"room_id": "20E", "inventory_slots": held},
        state={"room_id": "100", "inventory_slots": held},
    )
    assert result["divert"] is False
    assert result["step_success"] is True
    assert len(q.pending_capture_indices) == 1


def test_yawn_stairs_leave_bite_warp_without_crest_diverts():
    q = PlannerLoyalQueue()
    _seek_yawn_stairs_leave(q)
    result = q.evaluate_transition(
        prev_state={"room_id": "20E", "inventory_slots": [("shield_key", 1)]},
        state={"room_id": "100", "inventory_slots": [("shield_key", 1)]},
    )
    assert result["divert"] is True
    assert "wrong_traverse" in str(result["divert_reason"])


def test_yawn_bite_warp_tip_skips_20d_204_and_merges_at_204():
    q = PlannerLoyalQueue()
    stairs_i = next(
        i for i, s in enumerate(q._steps) if s.get("edge_id") == "20E->20D"
    )
    q.seek(stairs_i + 1)  # would be 20D->204 without skip
    held = [("moon_crest", 1), ("shotgun_shells", 7)]
    q.note_start_inventory({"room_id": "100", "inventory_slots": held})
    stay = q.evaluate_transition(
        prev_state={"room_id": "100", "inventory_slots": held},
        state={"room_id": "100", "inventory_slots": held},
    )
    assert stay["divert"] is False
    assert stay["step_success"] is False
    assert q.current.get("edge_id") == "100->101"
    assert q.current.get("yawn_bite_recovery") is True
    assert q.target_room(here="100") == "101"

    path = ["100", "101", "201", "202", "203", "204"]
    for a, b in zip(path, path[1:]):
        hop = q.evaluate_transition(
            prev_state={"room_id": a, "inventory_slots": held},
            state={"room_id": b, "inventory_slots": held},
        )
        assert hop["divert"] is False, hop
        assert hop["step_success"] is True
        # Mintable branch hops (not capture:false).
        assert q._steps[q.index - 1].get("capture") is not False

    assert q.current.get("edge_id") == "204->207"
    assert q.target_room(here="204") == "207"
    done = q.evaluate_transition(
        prev_state={"room_id": "204", "inventory_slots": held},
        state={"room_id": "207", "inventory_slots": held},
    )
    assert done["divert"] is False
    assert done["step_success"] is True


def test_yawn_bite_branch_tip_in_101_stays_on_recovery():
    """Minted 101 tip must not skip the rest of the bite corridor."""
    q = PlannerLoyalQueue()
    edge_i = next(
        i for i, s in enumerate(q._steps) if s.get("edge_id") == "101->201"
        and s.get("yawn_bite_recovery")
    )
    held = [("moon_crest", 1), ("shotgun_shells", 7)]
    q.seek(edge_i)
    q.note_start_inventory({"room_id": "101", "inventory_slots": held})
    assert q.current.get("edge_id") == "101->201"
    stay = q.evaluate_transition(
        prev_state={"room_id": "101", "inventory_slots": held},
        state={"room_id": "101", "inventory_slots": held},
    )
    assert stay["divert"] is False
    assert q.current.get("edge_id") == "101->201"
    hop = q.evaluate_transition(
        prev_state={"room_id": "101", "inventory_slots": held},
        state={"room_id": "201", "inventory_slots": held},
    )
    assert hop["step_success"] is True
    assert q.current.get("edge_id") == "201->202"


def test_yawn_bite_tip_in_100_skips_20d_without_crest_in_ram():
    """Room 100 tip arms bite branch even if crest RAM lags a frame."""
    q = PlannerLoyalQueue()
    stairs_i = next(
        i for i, s in enumerate(q._steps) if s.get("edge_id") == "20E->20D"
    )
    q.seek(stairs_i + 1)
    q.note_start_inventory({"room_id": "100", "inventory_slots": [("shield_key", 1)]})
    assert q._yawn_bite_recovery is True
    assert q.current.get("edge_id") == "100->101"


def test_yawn_bite_tip_save_room_floor_loot_diverts():
    """Room-100 bite tips still divert on off-plan ink_ribbon / serum."""
    q = PlannerLoyalQueue()
    stairs_i = next(
        i for i, s in enumerate(q._steps) if s.get("edge_id") == "20E->20D"
    )
    q.seek(stairs_i + 1)
    held = [("moon_crest", 1), ("shotgun_shells", 7)]
    q.note_start_inventory({"room_id": "100", "inventory_slots": held})
    assert q.current.get("edge_id") == "100->101"
    ink = q.evaluate_transition(
        prev_state={"room_id": "100", "inventory_slots": held},
        state={
            "room_id": "100",
            "inventory_slots": held + [("ink_ribbon", 1)],
        },
    )
    assert ink["divert"] is True, ink
    assert "ink_ribbon" in str(ink["divert_reason"])
    q2 = PlannerLoyalQueue()
    q2.seek(stairs_i + 1)
    q2.note_start_inventory({"room_id": "100", "inventory_slots": held})
    serum = q2.evaluate_transition(
        prev_state={"room_id": "100", "inventory_slots": held},
        state={
            "room_id": "100",
            "inventory_slots": held + [("serum", 1)],
        },
    )
    assert serum["divert"] is True, serum
    assert "serum" in str(serum["divert_reason"])


def test_yawn_bite_warp_mid_episode_leave_100_recovers():
    """Stairs warp mid-episode must arm recovery so 100→101 is not a divert."""
    q = PlannerLoyalQueue()
    stairs_i = next(
        i for i, s in enumerate(q._steps) if s.get("edge_id") == "20E->20D"
    )
    held = [("moon_crest", 1), ("shotgun_shells", 7)]
    q.seek(stairs_i)
    q.note_start_inventory({"room_id": "20E", "inventory_slots": held})
    warp = q.evaluate_transition(
        prev_state={"room_id": "20E", "inventory_slots": held},
        state={"room_id": "100", "inventory_slots": held},
    )
    assert warp["step_success"] is True
    leave = q.evaluate_transition(
        prev_state={"room_id": "100", "inventory_slots": held},
        state={"room_id": "101", "inventory_slots": held},
    )
    assert leave["divert"] is False, leave
    assert leave["step_success"] is True
    assert q.current.get("edge_id") == "101->201"


def test_yawn_normal_20d_tip_skips_bite_recovery_corridor():
    q = PlannerLoyalQueue()
    stairs_i = next(
        i for i, s in enumerate(q._steps) if s.get("edge_id") == "20E->20D"
    )
    q.seek(stairs_i + 1)
    held = [("moon_crest", 1)]
    q.note_start_inventory({"room_id": "20D", "inventory_slots": held})
    bad = q.evaluate_transition(
        prev_state={"room_id": "20D", "inventory_slots": held},
        state={"room_id": "100", "inventory_slots": held},
    )
    assert bad["divert"] is True
    ok = q.evaluate_transition(
        prev_state={"room_id": "20D", "inventory_slots": held},
        state={"room_id": "204", "inventory_slots": held},
    )
    assert ok["step_success"] is True
    # Bite-branch pls are skipped; next hop is shared rejoin.
    stay = q.evaluate_transition(
        prev_state={"room_id": "204", "inventory_slots": held},
        state={"room_id": "204", "inventory_slots": held},
    )
    assert stay["divert"] is False
    assert q.current.get("edge_id") == "204->207"


def test_yawn_branch_slots_rejoin_before_place_moon():
    from re1_rl.planner_loyal_cells import slot_index_for_completed_step

    q = PlannerLoyalQueue()
    steps = q._steps
    by_edge = {}
    for i, s in enumerate(steps):
        edge = s.get("edge_id")
        if edge == "20D->204" and int(s.get("n") or 0) >= 180:
            by_edge["normal"] = i
        if s.get("yawn_bite_recovery") and edge == "100->101":
            by_edge["bite0"] = i
        if s.get("yawn_bite_recovery") and edge == "203->204":
            by_edge["bite_last"] = i
        if edge == "204->207" and int(s.get("n") or 0) >= 180:
            by_edge["rejoin"] = i
        if s.get("beat_id") == "place_moon_crest":
            by_edge["place"] = i
    assert slot_index_for_completed_step(by_edge["normal"], steps) == 186
    assert slot_index_for_completed_step(by_edge["bite0"], steps) == 187
    assert slot_index_for_completed_step(by_edge["bite_last"], steps) == 191
    assert slot_index_for_completed_step(by_edge["rejoin"], steps) == 192
    assert slot_index_for_completed_step(by_edge["place"], steps) == 196


def test_ink_ribbon_use_diverts_when_not_planned():
    q = PlannerLoyalQueue()
    prev = {"room_id": "106", "inventory_slots": [("ink_ribbon", 2)]}
    cur = {"room_id": "106", "inventory_slots": [("ink_ribbon", 1)]}
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is True
    assert result["divert_reason"] == "unplanned_ink_ribbon_use"


def test_typewriter_save_diverts_when_not_planned():
    q = PlannerLoyalQueue()
    prev = {"room_id": "106", "inventory_slots": [("ink_ribbon", 1)]}
    cur = {"room_id": "106", "inventory_slots": []}
    result = q.evaluate_transition(
        prev_state=prev,
        state=cur,
        typewriter_save_complete=True,
    )
    assert result["divert"] is True
    assert result["divert_reason"] == "unplanned_typewriter_save"


def test_already_held_acquire_is_skipped():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test",
            "steps": [
                {"n": 1, "op": "acquire", "pickup_id": "106:ink_ribbon:1"},
                {"n": 2, "op": "traverse", "edge_id": "106->105"},
            ],
        }
    )
    q.note_start_inventory(
        {"inventory_slots": [("ink_ribbon", 1)]}
    )
    prev = {
        "room_id": "106",
        "inventory_slots": [("ink_ribbon", 1)],
    }
    cur = {
        "room_id": "105",
        "inventory_slots": [("ink_ribbon", 1)],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.done is True


def test_108_bullets_not_skipped_when_104_ammo_already_held():
    q = PlannerLoyalQueue()
    idx = next(
        i
        for i, step in enumerate(q._steps)
        if step.get("pickup_id") == "108:handgun_bullets:1"
    )
    q.seek(idx)
    q.note_start_inventory({"inventory_slots": [("handgun_bullets", 30)]})
    q._skip_satisfied_acquires()
    assert q.current is not None
    assert q.current["pickup_id"] == "108:handgun_bullets:1"


def test_second_bullet_pile_not_skipped_when_first_held():
    """pl08 resume: pile :2 must not auto-skip just because pile :1 ammo is held."""
    q = PlannerLoyalQueue()
    q.seek(3)  # step 4 = 104:handgun_bullets:2
    assert q.current["pickup_id"] == "104:handgun_bullets:2"
    q.note_start_inventory({"inventory_slots": [("handgun_bullets", 15)]})
    q._skip_satisfied_acquires()
    assert q.index == 3
    assert q.current["pickup_id"] == "104:handgun_bullets:2"


def test_ammo_stack_qty_increase_counts_as_gain():
    q = PlannerLoyalQueue()
    q.seek(3)
    prev = {
        "room_id": "104",
        "inventory_slots": [("beretta", 15), ("handgun_bullets", 15)],
    }
    cur = {
        "room_id": "104",
        "inventory_slots": [("beretta", 15), ("handgun_bullets", 30)],
        "new_items": ["handgun_bullets"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is True
    assert result["divert"] is False
    assert q.index == 4


def test_unique_key_acquire_completes_without_rising_edge():
    """File cinema can start skip after RAM already has the item (no qty edge)."""
    q = PlannerLoyalQueue()
    q.seek(5)  # step 6 = 10F:music_notes:1
    assert q.current["pickup_id"] == "10F:music_notes:1"
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("emblem", 1),
            ]
        }
    )
    slots = [
        ("knife", 1),
        ("beretta", 15),
        ("emblem", 1),
        ("music_notes", 0),
    ]
    result = q.evaluate_transition(
        prev_state={"room_id": "10F", "inventory_slots": slots},
        state={"room_id": "10F", "inventory_slots": slots},
    )
    assert result["step_success"] is True
    assert result["divert"] is False
    assert q.current["op"] == "objective"
    assert q.current["beat_id"] == "piano_play"


def test_unique_key_acquire_qty_zero_file_slot():
    """RE file items often occupy a slot with qty 0; that still counts as held."""
    q = PlannerLoyalQueue()
    q.seek(5)
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("emblem", 1),
            ]
        }
    )
    result = q.evaluate_transition(
        prev_state={
            "room_id": "10F",
            "inventory_slots": [("knife", 1), ("beretta", 15), ("emblem", 1)],
        },
        state={
            "room_id": "10F",
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("emblem", 1),
                ("music_notes", 0),
            ],
        },
    )
    assert result["step_success"] is True
    assert q.current["op"] == "objective"


def test_piano_play_completes_when_notes_leave_inventory() -> None:
    """USE can settle after skip; notes leaving 10F is the piano mint."""
    q = PlannerLoyalQueue()
    q.seek(6)  # piano_play
    assert q.current["site_id"] == "music_notes@10F_piano"
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("emblem", 1),
                ("music_notes", 0),
            ]
        }
    )
    prev = {
        "room_id": "10F",
        "inventory_slots": [
            ("knife", 1),
            ("beretta", 15),
            ("emblem", 1),
            ("music_notes", 0),
        ],
    }
    cur = {
        "room_id": "10F",
        "inventory_slots": [
            ("knife", 1),
            ("beretta", 15),
            ("emblem", 1),
        ],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.current["pickup_id"] == "10F:gold_emblem:2"


def test_piano_play_survives_same_frame_gold_emblem() -> None:
    """Live fail: gold_emblem pickup gate ran before piano_play could mint."""
    q = PlannerLoyalQueue()
    q.seek(6)
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("emblem", 1),
                ("music_notes", 0),
            ]
        }
    )
    prev = {
        "room_id": "10F",
        "inventory_slots": [
            ("knife", 1),
            ("beretta", 15),
            ("emblem", 1),
            ("music_notes", 0),
        ],
    }
    cur = {
        "room_id": "10F",
        "inventory_slots": [
            ("knife", 1),
            ("beretta", 15),
            ("emblem", 1),
            ("gold_emblem", 1),
        ],
        "story_use_success": "music_notes@10F_piano",
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.divert_reason is None
    assert q.current["pickup_id"] == "10F:gold_emblem:2"


def test_fireplace_use_mints_when_gold_leaves_even_if_shield_key_spawns() -> None:
    """Keep-playing after 104->105: gold USE often spawns shield_key before story_use."""
    q = PlannerLoyalQueue()
    q.seek(11)  # gold_emblem@105_fireplace
    assert q.current["site_id"] == "gold_emblem@105_fireplace"
    q.note_start_inventory(
        {
            "room_id": "105",
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("gold_emblem", 1),
            ],
        }
    )
    result = q.evaluate_transition(
        prev_state={
            "room_id": "105",
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("gold_emblem", 1),
            ],
        },
        state={
            "room_id": "105",
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("shield_key", 1),
            ],
        },
    )
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.current["pickup_id"] == "105:shield_key:2"


def test_star_crest_use_mints_when_crest_leaves_inventory() -> None:
    q = PlannerLoyalQueue()
    q.seek(45)  # place_star_crest
    assert q.current["site_id"] == "star_crest@11A_crest_slot"
    held = [
        ("knife", 1),
        ("shotgun", 3),
        ("star_crest", 1),
        ("chemical", 1),
    ]
    after = [
        ("knife", 1),
        ("shotgun", 3),
        ("chemical", 1),
    ]
    q.note_start_inventory({"room_id": "11A", "inventory_slots": held})
    result = q.evaluate_transition(
        prev_state={"room_id": "11A", "inventory_slots": held},
        state={"room_id": "11A", "inventory_slots": after},
    )
    assert result["divert"] is False
    assert result["step_success"] is True
    assert q.current["edge_id"] == "11A->10A"


def test_minted_shield_key_flicker_in_later_room_is_not_a_pickup() -> None:
    """Box-close cinema can re-show shield_key after leave_118. Not a new pile."""
    q = PlannerLoyalQueue()
    q.seek(25)  # 118->10B; satisfied pickups include 105:shield_key
    assert q.current["edge_id"] == "118->10B"
    q.note_start_inventory(
        {"room_id": "118", "inventory_slots": [("knife", 1)]}
    )
    result = q.evaluate_transition(
        prev_state={"room_id": "118", "inventory_slots": [("knife", 1)]},
        state={
            "room_id": "118",
            "inventory_slots": [("knife", 1), ("shield_key", 1)],
        },
    )
    assert result["divert"] is False
    assert result["step_success"] is False


def test_piano_play_gold_without_notes_use_still_diverts() -> None:
    q = PlannerLoyalQueue()
    q.seek(6)
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("emblem", 1),
                ("music_notes", 0),
            ]
        }
    )
    prev = {
        "room_id": "10F",
        "inventory_slots": [
            ("knife", 1),
            ("beretta", 15),
            ("emblem", 1),
            ("music_notes", 0),
        ],
    }
    cur = {
        "room_id": "10F",
        "inventory_slots": [
            ("knife", 1),
            ("beretta", 15),
            ("emblem", 1),
            ("music_notes", 0),
            ("gold_emblem", 1),
        ],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is True
    assert result["step_success"] is False
    assert q.divert_reason == "unplanned_pickup:['gold_emblem']"


def test_alcove_swap_completes_on_wooden_emblem_loss():
    """Yawn place_emblem_10F: wooden gone in 10F, gold still held."""
    q = PlannerLoyalQueue()
    q.seek(8)  # n=9 emblem@10F_alcove
    assert q.current["site_id"] == "emblem@10F_alcove"
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("knife", 1),
                ("beretta", 15),
                ("emblem", 1),
                ("gold_emblem", 1),
            ]
        }
    )
    held = [
        ("knife", 1),
        ("beretta", 15),
        ("emblem", 1),
        ("gold_emblem", 1),
    ]
    after = [
        ("knife", 1),
        ("beretta", 15),
        ("gold_emblem", 1),
    ]
    result = q.evaluate_transition(
        prev_state={"room_id": "10F", "inventory_slots": held},
        state={"room_id": "10F", "inventory_slots": after},
    )
    assert result["step_success"] is True
    assert result["divert"] is False
    assert q.current["op"] == "traverse"
    assert q.current["edge_id"] == "10F->104"


def test_alcove_swap_accepts_wall_story_site():
    q = PlannerLoyalQueue()
    q.seek(8)
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("emblem", 1),
                ("gold_emblem", 1),
            ]
        }
    )
    result = q.evaluate_transition(
        prev_state={
            "room_id": "10F",
            "inventory_slots": [("emblem", 1), ("gold_emblem", 1)],
        },
        state={
            "room_id": "10F",
            "inventory_slots": [("gold_emblem", 1)],
            "story_use_success": "emblem@10F_wall",
        },
    )
    assert result["step_success"] is True


def test_alcove_swap_completes_after_skip_without_rising_edge():
    """USE cinema can start skip after wooden emblem is already gone."""
    q = PlannerLoyalQueue()
    q.seek(8)
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("emblem", 1),
                ("gold_emblem", 1),
            ]
        }
    )
    after = [("gold_emblem", 1)]
    result = q.evaluate_transition(
        prev_state={"room_id": "10F", "inventory_slots": after},
        state={"room_id": "10F", "inventory_slots": after},
    )
    assert result["step_success"] is True


def test_alcove_swap_ignores_gold_emblem_putback():
    q = PlannerLoyalQueue()
    q.seek(8)
    q.note_start_inventory(
        {
            "inventory_slots": [
                ("emblem", 1),
                ("gold_emblem", 1),
            ]
        }
    )
    result = q.evaluate_transition(
        prev_state={
            "room_id": "10F",
            "inventory_slots": [("emblem", 1), ("gold_emblem", 1)],
        },
        state={"room_id": "10F", "inventory_slots": [("emblem", 1)]},
    )
    assert result["step_success"] is False


def _gallery_chunk() -> dict:
    return {
        "chunk_id": "gallery",
        "end_anchor_beat_id": "star_crest",
        "steps": [
            {
                "n": 1,
                "op": "do_puzzle",
                "site_id": "gallery_portrait_1",
                "room_id": "117",
                "beat_id": "gallery_portrait_1",
            },
            {
                "n": 2,
                "op": "do_puzzle",
                "site_id": "gallery_portrait_2",
                "room_id": "117",
                "beat_id": "gallery_portrait_2",
            },
            {
                "n": 3,
                "op": "acquire",
                "pickup_id": "117:star_crest:1",
                "room_id": "117",
                "beat_id": "star_crest",
            },
        ],
    }


def test_gallery_portrait_completes_on_progress():
    q = PlannerLoyalQueue(_gallery_chunk())
    q.note_start_inventory({"room_id": "117", "gallery_progress": 0})
    prev = {"room_id": "117", "gallery_progress": 0, "inventory_slots": []}
    cur = {
        "room_id": "117",
        "gallery_progress": GALLERY_STEP_VALUES[0],
        "inventory_slots": [],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is True
    assert q.current["beat_id"] == "gallery_portrait_2"


def test_gallery_portraits_already_done_are_skipped():
    q = PlannerLoyalQueue(_gallery_chunk())
    q.note_start_inventory(
        {"room_id": "117", "gallery_progress": GALLERY_STEP_VALUES[1]}
    )
    prev = {
        "room_id": "117",
        "gallery_progress": GALLERY_STEP_VALUES[1],
        "inventory_slots": [],
    }
    cur = {
        "room_id": "117",
        "gallery_progress": GALLERY_STEP_VALUES[1],
        "inventory_slots": [("star_crest", 1)],
        "new_items": ["star_crest"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is True
    assert q.done is True


def test_gallery_portrait_compass_uses_rdt_targets():
    xz = _planner_step_target_xz(
        {
            "op": "do_puzzle",
            "site_id": "gallery_portrait_3",
            "beat_id": "gallery_portrait_3",
        }
    )
    assert xz == GALLERY_TARGETS[2]
    crest = _planner_step_target_xz(
        {"op": "acquire", "pickup_id": "117:star_crest:1"}
    )
    assert crest == GALLERY_FINAL_SWITCH_TARGET
    end_life = _planner_step_target_xz(
        {
            "op": "do_puzzle",
            "site_id": "gallery_end_of_life",
            "beat_id": "gallery_end_of_life",
        }
    )
    assert end_life == GALLERY_FINAL_SWITCH_TARGET


def test_gallery_end_of_life_completes_at_final_switch():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "eol",
            "end_anchor_beat_id": "star_crest",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "gallery_end_of_life",
                    "room_id": "117",
                    "beat_id": "gallery_end_of_life",
                },
                {
                    "n": 2,
                    "op": "acquire",
                    "pickup_id": "117:star_crest:1",
                    "room_id": "117",
                    "beat_id": "star_crest",
                },
            ],
        }
    )
    q.note_start_inventory(
        {"room_id": "117", "gallery_progress": GALLERY_STEP_VALUES[5]}
    )
    fx, fz = GALLERY_FINAL_SWITCH_TARGET
    ox, oz = GALLERY_TARGETS[5]
    prev = {
        "room_id": "117",
        "gallery_progress": GALLERY_STEP_VALUES[5],
        "x": ox,
        "z": oz,
        "inventory_slots": [],
    }
    away = {
        "room_id": "117",
        "gallery_progress": 0,
        "x": ox,
        "z": oz,
        "inventory_slots": [],
    }
    assert q.evaluate_transition(prev_state=prev, state=away)["step_success"] is False
    at_switch = {
        "room_id": "117",
        "gallery_progress": 0,
        "x": fx,
        "z": fz,
        "inventory_slots": [],
    }
    result = q.evaluate_transition(prev_state=prev, state=at_switch)
    assert result["step_success"] is True
    assert q.current["beat_id"] == "star_crest"


def test_gallery_end_of_life_completes_on_cre1_settle_one():
    """Live C-RE1 death-painting RAM is 2→1 at the switch, not 2→0."""
    q = PlannerLoyalQueue(
        {
            "chunk_id": "eol",
            "end_anchor_beat_id": "star_crest",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "gallery_end_of_life",
                    "room_id": "117",
                    "beat_id": "gallery_end_of_life",
                },
                {
                    "n": 2,
                    "op": "acquire",
                    "pickup_id": "117:star_crest:1",
                    "room_id": "117",
                    "beat_id": "star_crest",
                },
            ],
        }
    )
    q.note_start_inventory(
        {"room_id": "117", "gallery_progress": GALLERY_STEP_VALUES[5]}
    )
    fx, fz = GALLERY_FINAL_SWITCH_TARGET
    prev = {
        "room_id": "117",
        "gallery_progress": GALLERY_STEP_VALUES[5],
        "x": fx,
        "z": fz,
        "inventory_slots": [],
    }
    settle = {
        "room_id": "117",
        "gallery_progress": 1,
        "x": fx,
        "z": fz,
        "inventory_slots": [],
    }
    result = q.evaluate_transition(prev_state=prev, state=settle)
    assert result["step_success"] is True
    assert q.current["beat_id"] == "star_crest"


def test_ammo_new_slot_counts_as_gain():
    q = PlannerLoyalQueue()
    q.seek(2)  # first bullet pile
    prev = {
        "room_id": "104",
        "inventory_slots": [("beretta", 15)],
    }
    cur = {
        "room_id": "104",
        "inventory_slots": [("beretta", 15), ("handgun_bullets", 15)],
        "new_items": ["handgun_bullets"],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["step_success"] is True
    assert q.index == 3


def test_planner_loyal_curriculum_is_not_yawn_rails():
    import json

    stage = json.loads(
        (PROJECT_ROOT / "curriculum" / "planner_loyal_one_leg.json").read_text(
            encoding="utf-8"
        )
    )
    assert stage["mode"] == "planner_loyal"
    assert stage["route_steps"] == []


def test_reset_wrapper_skips_yawn_sampler_when_planner_loyal(monkeypatch):
    import gymnasium as gym
    from gymnasium import spaces

    from re1_rl.go_explore_reset_wrapper import GoExploreResetWrapper

    sampled: list[int] = []
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    monkeypatch.setattr(
        "re1_rl.yawn_rails.sample_one_leg_options",
        lambda *a, **k: sampled.append(1) or {"route_start_index": 121},
    )

    class _StubEnv(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self) -> None:
            self.observation_space = spaces.Discrete(1)
            self.action_space = spaces.Discrete(1)
            self.curriculum_path = (
                PROJECT_ROOT / "curriculum" / "yawn_rails_one_leg.json"
            )
            self.project_root = PROJECT_ROOT
            self.last_options = None

        def reset(self, *, seed=None, options=None):
            self.last_options = options
            return 0, {}

        def step(self, action):
            return 0, 0.0, False, False, {}

    inner = _StubEnv()
    wrapper = GoExploreResetWrapper(inner, project_root=PROJECT_ROOT)
    wrapper.reset()
    assert sampled == []
    assert "route_start_index" not in (inner.last_options or {})


def test_reset_wrapper_skips_pb_mix_for_planner_loyal_mode(monkeypatch):
    """mode=planner_loyal must not inject champion.State (kills C-RE1 actors)."""
    import gymnasium as gym
    from gymnasium import spaces

    from re1_rl.go_explore_reset_wrapper import GoExploreResetWrapper

    injected: list[dict] = []
    monkeypatch.delenv("RE1_PLANNER_LOYAL", raising=False)
    monkeypatch.setattr(
        "re1_rl.pb_curriculum.sample_training_start",
        lambda *a, **k: injected.append({"state_path": "champion.State"})
        or {"state_path": "champion.State", "sidecar_path": "x.json"},
    )

    class _StubEnv(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self) -> None:
            self.observation_space = spaces.Discrete(1)
            self.action_space = spaces.Discrete(1)
            self.curriculum_path = (
                PROJECT_ROOT / "curriculum" / "planner_loyal_one_leg.json"
            )
            self.project_root = PROJECT_ROOT
            self.last_options = None

        def reset(self, *, seed=None, options=None):
            self.last_options = options
            return 0, {}

        def step(self, action):
            return 0, 0.0, False, False, {}

    inner = _StubEnv()
    wrapper = GoExploreResetWrapper(inner, project_root=PROJECT_ROOT, pb_weight=1.0)
    wrapper.reset()
    assert injected == []
    assert "pb_bundle" not in (inner.last_options or {})


def test_encode_dim_stable():
    q = PlannerLoyalQueue()
    vec = encode_planner_queue(q)
    assert len(vec) == PLANNER_QUEUE_DIM
    assert vec[0] == 1.0


def test_reward_step_success_pays_eight_and_pops():
    q = PlannerLoyalQueue()
    progress = ProgressTracker(leg_span=1)
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_step_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert bd["checkpoint_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert q.current["edge_id"] == "105->104"
    assert progress.checkpoint_success is False
    assert reward == STEP_PENALTY + PLANNER_STEP_SUCCESS_REWARD
    # Mid-chunk rearms a fresh 12m wall and extends the episode, not a reset.
    from re1_rl.yawn_cell_timeout import FLAT_CELL_TIMEOUT_FRAMES

    assert progress.cell_timeout_frames == FLAT_CELL_TIMEOUT_FRAMES
    assert progress.leg_emulated_frames == 0
    assert progress.max_steps_bonus == CHECKPOINT_MAX_STEPS_EXTENSION
    assert progress.stagnation_frames == 0


def test_reward_step_success_scales_with_leftover_time():
    q = PlannerLoyalQueue()
    progress = ProgressTracker(leg_span=1)
    from re1_rl.yawn_cell_timeout import FLAT_CELL_TIMEOUT_FRAMES

    progress.arm_cell_timeout(1000)
    progress.note_leg_frames(500)
    prev = {
        "room_id": "106",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
    }
    cur = {
        "room_id": "105",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "step_emulated_frames": 0,
    }
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_step_success"] == pytest.approx(4.0)
    assert bd["checkpoint_success"] == pytest.approx(4.0)
    # Next step gets a fresh full 12m budget.
    assert progress.cell_timeout_frames == FLAT_CELL_TIMEOUT_FRAMES
    assert progress.leg_emulated_frames == 0


def test_reward_wrong_room_pays_minus_four_and_is_terminal():
    q = PlannerLoyalQueue()
    progress = ProgressTracker()
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "107", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["wrong_room"] == PLANNER_DIVERT_PENALTY
    assert progress.wrong_room_breached is True
    assert reward == STEP_PENALTY + PLANNER_DIVERT_PENALTY
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "yawn_rails", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "planner_divert"


def test_planner_loyal_kenneth_gate_kills_fresh_start_hall() -> None:
    """105→106 before 104:*:sN is the Kenneth gate, not a generic divert."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    progress = ProgressTracker()
    prev = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert progress.kenneth_gate_breached is True
    assert bd["main_hall_before_kenneth"] == MAIN_HALL_BEFORE_KENNETH_PENALTY
    assert bd["planner_divert"] == 0.0
    assert reward == pytest.approx(STEP_PENALTY + MAIN_HALL_BEFORE_KENNETH_PENALTY)
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "planner_loyal", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "main_hall_before_kenneth"


def test_planner_loyal_dining_return_without_kenneth_is_terminal() -> None:
    """104→105 before 104:*:sN is a divert; the cinema must play first."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)  # 104->105
    progress = ProgressTracker()
    prev = {"room_id": "104", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["planner_step_success"] == 0.0
    assert q.divert_reason == "barry_return_before_kenneth"
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "planner_loyal", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "barry_return_before_kenneth"
    assert reward == pytest.approx(STEP_PENALTY + PLANNER_DIVERT_PENALTY)


def test_planner_loyal_kenneth_skip_peak_unlocks_dining_return() -> None:
    """C-RE1 turbo Kenneth settles idle 0x80; mid-skip 0x84 still writes 104:*:sN."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)
    progress = ProgressTracker()
    idle = {
        "room_id": "104",
        "cam_id": 4,
        "scene_flag": 0x80,
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "_skip_peak_scene_flag": 0x84,
    }
    _reward(idle, idle, q, progress=progress)
    assert "104:4:s0" in progress.observed_cutscenes
    dining = {
        "room_id": "105",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
    }
    _reward_total, bd = _reward(idle, dining, q, progress=progress)
    assert bd["planner_divert"] == 0.0
    assert bd["planner_step_success"] > 0.0
    assert q.divert_reason is None


def test_planner_loyal_dining_return_after_kenneth_completes() -> None:
    """This-leg 104:*:sN unlocks 104→105 with no clips in inventory."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)
    progress = ProgressTracker()
    progress.note_leg_cutscene("104:4:s0")
    prev = {"room_id": "104", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == 0.0
    assert bd["planner_step_success"] > 0.0
    assert q.divert_reason is None


def test_pl03_barry_return_fails_on_hp_damage() -> None:
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)
    progress = ProgressTracker()
    progress.note_leg_cutscene("104:4:s0")
    prev = {"room_id": "104", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "104", "inventory_slots": [], "hp": 88, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["planner_step_success"] == 0.0
    assert q.divert_reason == "barry_return_combat"
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "planner_loyal", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "barry_return_combat"
    assert reward < 0.0
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY


def test_pl03_barry_return_fails_on_denied_combat_events() -> None:
    """104 zeroes enemy_damage pay; combat_events still count as dealing damage."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)
    progress = ProgressTracker()
    progress.note_leg_cutscene("104:4:s0")
    prev = {"room_id": "104", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {
        "room_id": "104",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "enemy_damage": 0,
        "enemy_kills": 0,
        "combat_events": [
            {"slot": 0, "damage": 5, "killed": False, "reward_denied": True}
        ],
    }
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert q.divert_reason == "barry_return_combat"


def test_pl03_barry_return_clean_traverse_still_succeeds() -> None:
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)
    progress = ProgressTracker()
    progress.note_leg_cutscene("104:4:s0")
    prev = {
        "room_id": "104",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "combat_events": [],
    }
    cur = {
        "room_id": "105",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "combat_events": [],
    }
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == 0.0
    assert bd["planner_step_success"] > 0.0
    assert q.divert_reason is None
    assert q.current["edge_id"] == "105->106"


def test_planner_loyal_door_cam_flag_does_not_unlock_dining_return() -> None:
    """This-leg 104:0:s0 is the tea-room door, not Kenneth."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)
    progress = ProgressTracker()
    progress.note_leg_cutscene("104:0:s0")
    prev = {"room_id": "104", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["planner_step_success"] == 0.0
    assert q.divert_reason == "barry_return_before_kenneth"


def test_planner_loyal_inherited_kenneth_flag_does_not_unlock_dining_return() -> None:
    """Predecessor sidecar 104:*:sN is history, not this-leg Kenneth (cp02 rule)."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)  # 104->105
    progress = ProgressTracker()
    progress.observe_cutscene("104:4:s0")
    progress.rewarded_cutscenes.add("104:4:s0")
    assert not progress.leg_observed_cutscenes
    prev = {"room_id": "104", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["planner_step_success"] == 0.0
    assert q.divert_reason == "barry_return_before_kenneth"
    assert q.current["edge_id"] == "104->105"
    assert reward == pytest.approx(STEP_PENALTY + PLANNER_DIVERT_PENALTY)
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "planner_loyal", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "barry_return_before_kenneth"


def test_planner_loyal_already_in_dining_without_leg_kenneth_diverts() -> None:
    """Already-in-dest 104->105 with no this-leg Kenneth is terminal, not a silent wait."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)  # 104->105
    progress = ProgressTracker()
    progress.observe_cutscene("104:4:s0")  # inherited only
    prev = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["planner_step_success"] == 0.0
    assert q.divert_reason == "barry_return_before_kenneth"
    assert reward == pytest.approx(STEP_PENALTY + PLANNER_DIVERT_PENALTY)
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "planner_loyal", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "barry_return_before_kenneth"


def test_planner_loyal_already_in_dining_with_leg_kenneth_completes() -> None:
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(2)
    progress = ProgressTracker()
    progress.note_leg_cutscene("104:4:s0")
    prev = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == 0.0
    assert bd["planner_step_success"] > 0.0
    assert q.divert_reason is None
    assert q.current["edge_id"] == "105->106"


def test_opening_tea_room_clips_divert_until_live_acquire() -> None:
    """Opening 105->104 is a walk. Live chunk comes back for the 104 piles."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(1)  # 105->104 kenneth
    result = q.evaluate_transition(
        prev_state={"room_id": "104", "inventory_slots": [("beretta", 15)]},
        state={
            "room_id": "104",
            "inventory_slots": [("beretta", 15), ("handgun_bullets", 15)],
            "new_items": ["handgun_bullets"],
        },
    )
    assert result["divert"] is True
    assert "unplanned_pickup" in str(result["divert_reason"])
    assert "handgun_bullets" in str(result["divert_reason"])


def test_acquire_plus_extra_ammo_is_unplanned_pickup() -> None:
    """Chemical is the step; a same-frame clip still ends the episode."""
    q = PlannerLoyalQueue()
    q.seek(23)  # 118:chemical:1
    assert q.current["pickup_id"] == "118:chemical:1"
    result = q.evaluate_transition(
        prev_state={"room_id": "118", "inventory_slots": [("beretta", 15)]},
        state={
            "room_id": "118",
            "inventory_slots": [
                ("beretta", 15),
                ("chemical", 1),
                ("handgun_bullets", 15),
            ],
            "new_items": ["chemical", "handgun_bullets"],
        },
    )
    assert result["divert"] is True
    assert "handgun_bullets" in str(result["divert_reason"])
    assert result["step_success"] is False


def test_planner_loyal_wesker_bounce_skip_kills_without_settle_in_hall() -> None:
    """105→106→105 turbo bounce still trips the Kenneth gate."""
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    progress = ProgressTracker()
    prev = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {
        "room_id": "105",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "_skip_peak_room": "106",
    }
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert progress.kenneth_gate_breached is True
    assert bd["main_hall_before_kenneth"] == MAIN_HALL_BEFORE_KENNETH_PENALTY
    assert bd["planner_divert"] == 0.0
    assert reward == pytest.approx(STEP_PENALTY + MAIN_HALL_BEFORE_KENNETH_PENALTY)
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "planner_loyal", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "main_hall_before_kenneth"


def test_planner_loyal_kenneth_gate_allows_hall_after_tea_room_flag() -> None:
    opening = PROJECT_ROOT / "data" / "planner_chunks" / "opening_to_lockpick.json"
    q = PlannerLoyalQueue(chunk_path=opening)
    q.seek(3)  # 105->106 after Kenneth
    progress = ProgressTracker()
    progress.observe_cutscene("104:0:s0")
    prev = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert progress.kenneth_gate_breached is False
    assert bd["main_hall_before_kenneth"] == 0.0
    assert bd["planner_step_success"] > 0.0


def test_reward_wrong_gallery_portrait_pays_minus_four_and_is_terminal():
    """pl45-style: Yes on a bad painting resets RAM and ends like yawn cells."""
    q = PlannerLoyalQueue(
        {
            "chunk_id": "gallery_old_man",
            "end_anchor_beat_id": "gallery_portrait_6",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "gallery_portrait_6",
                    "room_id": "117",
                    "beat_id": "gallery_portrait_6",
                }
            ],
        }
    )
    progress = ProgressTracker()
    prev = {
        "room_id": "117",
        "gallery_progress": GALLERY_STEP_VALUES[4],
        "gallery_confirm": 0,
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "x": 16000.0,
        "z": 7200.0,
    }
    cur = {
        **prev,
        "gallery_progress": 0,
        "gallery_confirm": 4,
    }
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["gallery_wrong"] == pytest.approx(-GALLERY_WRONG_PORTRAIT_PENALTY)
    assert bd["planner_step_success"] == 0.0
    assert bd["planner_divert"] == 0.0
    assert progress.gallery_wrong_breached is True
    assert reward == STEP_PENALTY - GALLERY_WRONG_PORTRAIT_PENALTY
    terminated, _truncated, reason = RE1Env._termination_flags(
        SimpleNamespace(
            _stage={"mode": "yawn_rails", "max_steps": 3000},
            _progress=progress,
            _checkpoint_captured=False,
            _episode_failure_override=None,
            _planner_loyal_queue=q,
            _step_count=3,
            _episode_truncated=lambda: False,
        ),
        {"dead": False},
    )
    assert terminated is True
    assert reason == "gallery_wrong_portrait"


def test_reward_correct_old_man_does_not_gallery_wrong():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "gallery_old_man",
            "end_anchor_beat_id": "gallery_portrait_6",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "gallery_portrait_6",
                    "room_id": "117",
                    "beat_id": "gallery_portrait_6",
                }
            ],
        }
    )
    progress = ProgressTracker()
    prev = {
        "room_id": "117",
        "gallery_progress": GALLERY_STEP_VALUES[4],
        "gallery_confirm": 0,
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "x": 7250.0,
        "z": 6100.0,
    }
    cur = {**prev, "gallery_progress": GALLERY_STEP_VALUES[5], "gallery_confirm": 2}
    _reward_total, bd = _reward(prev, cur, q, progress=progress)
    assert bd["gallery_wrong"] == 0.0
    assert bd["planner_step_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert progress.gallery_wrong_breached is False


def test_reward_leave_gallery_is_divert_not_double_gallery_wrong():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "gallery_old_man",
            "end_anchor_beat_id": "gallery_portrait_6",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "gallery_portrait_6",
                    "room_id": "117",
                    "beat_id": "gallery_portrait_6",
                }
            ],
        }
    )
    progress = ProgressTracker()
    prev = {
        "room_id": "117",
        "gallery_progress": GALLERY_STEP_VALUES[4],
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
    }
    cur = {**prev, "room_id": "10A"}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["gallery_wrong"] == 0.0
    assert reward == STEP_PENALTY + PLANNER_DIVERT_PENALTY


def test_rails_mode_loyal_traverse_does_not_breach_wrong_room():
    """106→105 must credit planner_step_success even if legacy planner is on 210."""
    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    yawn_route = PROJECT_ROOT / "data" / "yawn_checkpoint_route.json"
    planner = WaypointPlanner(
        yawn_route,
        route_steps=list(range(1, 123)),
        start_index=121,
    )
    assert planner.next_waypoint_room() == "210"
    q = PlannerLoyalQueue()
    progress = ProgressTracker(leg_span=1)
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = compute_reward(
        prev,
        cur,
        planner,
        progress=progress,
        graph=graph,
        rails_mode=True,
        planner_loyal_queue=q,
        return_breakdown=True,
    )
    assert bd["wrong_room"] == 0.0
    assert progress.wrong_room_breached is False
    assert bd["planner_step_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert bd["checkpoint_success"] == PLANNER_STEP_SUCCESS_REWARD
    assert reward == STEP_PENALTY + PLANNER_STEP_SUCCESS_REWARD


def test_divert_alias_net_is_minus_four_under_rails_mode():
    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    planner = WaypointPlanner(ROUTE, waypoints=["210"])
    q = PlannerLoyalQueue()
    progress = ProgressTracker()
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "107", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = compute_reward(
        prev,
        cur,
        planner,
        progress=progress,
        graph=graph,
        rails_mode=True,
        planner_loyal_queue=q,
        return_breakdown=True,
    )
    assert bd["planner_divert"] == PLANNER_DIVERT_PENALTY
    assert bd["wrong_room"] == PLANNER_DIVERT_PENALTY
    assert reward == STEP_PENALTY + PLANNER_DIVERT_PENALTY


def test_episode_failure_context_includes_divert_and_target():
    q = PlannerLoyalQueue()
    q.divert_reason = "unplanned_pickup:['ink_ribbon']"
    env = SimpleNamespace(
        _planner_loyal_queue=q,
        _planner=SimpleNamespace(next_waypoint_room=lambda: "210"),
    )
    ctx = RE1Env._episode_failure_context(env, "planner_divert")
    assert ctx["planner_divert_reason"] == "unplanned_pickup:['ink_ribbon']"
    assert ctx["failure_target"] == "105"
    assert RE1Env._episode_failure_context(env, None) == {}


def test_reward_heal_use_tax_fires():
    q = PlannerLoyalQueue()
    assert HEAL_USE_TAX_LIGHT == pytest.approx(-0.10)
    prev = {
        "room_id": "106",
        "inventory_slots": [{"name": "green_herb"}],
        "hp": 40,
        "in_control": True,
    }
    cur = {
        "room_id": "106",
        "inventory_slots": [],
        "hp": 60,
        "in_control": True,
    }
    _reward_total, bd = _reward(prev, cur, q)
    assert bd["heal_use_tax"] == HEAL_USE_TAX_LIGHT


def test_reward_low_ammo_reload_pays():
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "106",
        "inventory_slots": [("beretta", 5), ("handgun_bullets", 30)],
        "hp": 96,
        "in_control": True,
    }
    cur = {
        "room_id": "106",
        "inventory_slots": [("beretta", 15), ("handgun_bullets", 20)],
        "hp": 96,
        "in_control": True,
    }
    _reward_total, bd = _reward(prev, cur, q)
    assert bd["weapon_reload"] == pytest.approx(WEAPON_RELOAD_REWARD)


def test_reward_reload_above_one_third_does_not_pay():
    q = PlannerLoyalQueue()
    prev = {
        "room_id": "106",
        "inventory_slots": [("beretta", 6), ("handgun_bullets", 30)],
        "hp": 96,
        "in_control": True,
    }
    cur = {
        "room_id": "106",
        "inventory_slots": [("beretta", 15), ("handgun_bullets", 21)],
        "hp": 96,
        "in_control": True,
    }
    _reward_total, bd = _reward(prev, cur, q)
    assert bd["weapon_reload"] == 0.0


def test_apply_obs_drops_strategy_keys_and_scalpels_world():
    from re1_rl.obs_encoder import GOAL_DIM, PROPRIO_DIM, PROPRIO_FIELDS
    from re1_rl.planner_loyal import PLANNER_LOYAL_OMIT_OBS_KEYS

    q = PlannerLoyalQueue()
    room_idx = next(i for i, (name, _) in enumerate(PROPRIO_FIELDS) if name == "room_index")
    proprio = np.zeros(PROPRIO_DIM, dtype=np.float32)
    proprio[room_idx] = 0.5

    obs = apply_planner_loyal_obs(
        {
            "goal": np.ones(GOAL_DIM, dtype=np.float32),
            "proprio": proprio,
            "world_state": np.ones(8, dtype=np.float32),
            "rooms_visited": np.ones(8, dtype=np.float32),
            "history": np.ones(8, dtype=np.float32),
            "acquisitions": np.ones(8, dtype=np.float32),
            "cutscene_ledger": np.ones(8, dtype=np.float32),
            "maps_files": np.ones(8, dtype=np.float32),
            "milestones": np.ones(8, dtype=np.float32),
            "affordances": np.ones(8, dtype=np.float32),
            "spatial": np.ones(8, dtype=np.float32),
            "named_state": np.ones(8, dtype=np.float32),
        },
        q,
    )
    for key in PLANNER_LOYAL_OMIT_OBS_KEYS:
        assert key not in obs
    assert "planner_steps" in obs
    assert obs["planner_steps"].shape == (PLANNER_QUEUE_DIM,)
    assert np.allclose(obs["spatial"], 1.0)
    assert np.allclose(obs["named_state"], 1.0)


def test_queue_pop_slides_remaining_encoding():
    q = PlannerLoyalQueue()
    first = encode_planner_queue(q)
    assert first[0] == 1.0
    q._index = 1
    second = encode_planner_queue(q)
    assert second[0] == 1.0
    assert first != second
    assert len(q.remaining) == len(q._steps) - 1


def test_apply_obs_adds_planner_steps_and_prunes_admin():
    q = PlannerLoyalQueue()
    from re1_rl.obs_encoder import GOAL_DIM, GOAL_FIELDS

    admin = {
        name: i
        for i, (name, _) in enumerate(GOAL_FIELDS)
        if name
        in {
            "waypoint_index",
            "waypoints_remaining",
            "curriculum_stage",
            "item_todo_progress",
            "wrong_room_flag",
        }
    }
    goal = np.ones(GOAL_DIM, dtype=np.float32)
    obs = apply_planner_loyal_obs({"goal": goal}, q)
    assert obs["planner_steps"].shape == (PLANNER_QUEUE_DIM,)
    assert obs["planner_steps"][0] == 1.0
    for index in admin.values():
        assert obs["goal"][index] == 0.0
    assert apply_planner_loyal_obs({"goal": goal.copy()}, None)["goal"][admin["waypoint_index"]] == 1.0
    pruned = prune_route_admin_goal(np.ones(GOAL_DIM, dtype=np.float32))
    assert pruned[admin["waypoint_index"]] == 0.0
    assert pruned[0] == 1.0


def test_planner_loyal_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("RE1_PLANNER_LOYAL", raising=False)
    assert planner_loyal_enabled() is False
    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    assert planner_loyal_enabled() is True


def test_finish_capture_hook_records_step_and_does_not_stick(monkeypatch, tmp_path):
    q = PlannerLoyalQueue()
    q._index = 1
    progress = ProgressTracker(leg_span=1)
    progress.checkpoint_success = True

    def _fake_capture(env, state, breakdown, **kwargs):
        return {
            "source": "planner_loyal",
            "checkpoint_index": 6,
            "room_id": state.get("room_id"),
        }

    monkeypatch.setattr(
        "re1_rl.planner_loyal_cells.capture_planner_loyal_cell", _fake_capture
    )
    env = SimpleNamespace(
        project_root=str(tmp_path),
        _planner_loyal_queue=q,
        _progress=progress,
        _checkpoint_freeze_pending=True,
        _checkpoint_captured=False,
        _macro_active=True,
        _yawn_rails_capture_pending=None,
        _apply_yawn_capture_ineligibility_penalty=lambda _bd: None,
        _planner_loyal_last_success=None,
    )
    env._maybe_capture_planner_loyal_cell = (
        lambda state, breakdown: RE1Env._maybe_capture_planner_loyal_cell(
            env, state, breakdown
        )
    )
    RE1Env._finish_checkpoint_capture(
        env, {"room_id": "105"}, {"checkpoint_success": 8.0}
    )
    assert env._planner_loyal_last_success["chunk_id"] == "cp05_shield_key"
    assert env._planner_loyal_last_success["completed_index"] == 0
    assert env._checkpoint_captured is False
    assert env._progress.checkpoint_success is False
    assert env._yawn_rails_capture_pending
    assert env._yawn_rails_capture_pending[0]["source"] == "planner_loyal"


def test_finish_capture_ends_episode_on_chunk_complete(monkeypatch, tmp_path):
    q = PlannerLoyalQueue()
    q._index = len(q._steps)
    progress = ProgressTracker(leg_span=1)
    progress.checkpoint_success = True

    monkeypatch.setattr(
        "re1_rl.planner_loyal_cells.capture_planner_loyal_cell",
        lambda *a, **k: {"source": "planner_loyal", "chunk_final": True},
    )
    env = SimpleNamespace(
        project_root=str(tmp_path),
        _planner_loyal_queue=q,
        _progress=progress,
        _checkpoint_freeze_pending=True,
        _checkpoint_captured=False,
        _macro_active=True,
        _yawn_rails_capture_pending=None,
        _apply_yawn_capture_ineligibility_penalty=lambda _bd: None,
        _planner_loyal_last_success=None,
        _episode_failure_override=None,
        _stage={"mode": "yawn_rails", "max_steps": 3000},
        _step_count=10,
        _episode_truncated=lambda: False,
    )
    env._maybe_capture_planner_loyal_cell = (
        lambda state, breakdown: RE1Env._maybe_capture_planner_loyal_cell(
            env, state, breakdown
        )
    )
    RE1Env._finish_checkpoint_capture(
        env, {"room_id": "105"}, {"checkpoint_success": 8.0}
    )
    assert q.done
    assert env._checkpoint_captured is True
    assert env._progress.checkpoint_success is True
    terminated, _trunc, reason = RE1Env._termination_flags(env, {"dead": False})
    assert terminated is True
    assert reason == "planner_chunk_complete"


def test_same_room_acquires_queue_every_pl_for_capture():
    """Clip then shells then the door must each stay queued for a cell mint."""
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test_102",
            "steps": [
                {
                    "n": 1,
                    "op": "acquire",
                    "pickup_id": "102:handgun_bullets:1",
                    "room_id": "102",
                },
                {
                    "n": 2,
                    "op": "acquire",
                    "pickup_id": "102:shotgun_shells:2",
                    "room_id": "102",
                },
                {"n": 3, "op": "traverse", "edge_id": "102->101"},
            ],
        }
    )
    q.note_start_inventory({"room_id": "102", "inventory_slots": [("beretta", 0)]})
    clip = q.evaluate_transition(
        prev_state={"room_id": "102", "inventory_slots": [("beretta", 0)]},
        state={
            "room_id": "102",
            "inventory_slots": [("beretta", 0), ("handgun_bullets", 15)],
            "new_items": ["handgun_bullets"],
        },
    )
    assert clip["step_success"] is True
    shells = q.evaluate_transition(
        prev_state={
            "room_id": "102",
            "inventory_slots": [("beretta", 0), ("handgun_bullets", 15)],
        },
        state={
            "room_id": "102",
            "inventory_slots": [
                ("beretta", 0),
                ("handgun_bullets", 15),
                ("shotgun_shells", 15),
            ],
            "new_items": ["shotgun_shells"],
        },
    )
    assert shells["step_success"] is True
    walk = q.evaluate_transition(
        prev_state={
            "room_id": "102",
            "inventory_slots": [
                ("beretta", 0),
                ("handgun_bullets", 15),
                ("shotgun_shells", 15),
            ],
        },
        state={
            "room_id": "101",
            "inventory_slots": [
                ("beretta", 0),
                ("handgun_bullets", 15),
                ("shotgun_shells", 15),
            ],
        },
    )
    assert walk["step_success"] is True
    assert q.pending_capture_indices == [0, 1, 2]


def test_capture_false_richard_is_not_queued_for_a_pl():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test_richard",
            "steps": [
                {
                    "n": 1,
                    "op": "trigger_cutscene",
                    "site_id": "20D:richard",
                    "room_id": "20D",
                    "beat_id": "richard_bleedout",
                    "capture": False,
                },
                {"n": 2, "op": "traverse", "edge_id": "204->207"},
            ],
        }
    )
    result = q.evaluate_transition(
        prev_state={"room_id": "20D", "inventory_slots": []},
        state={
            "room_id": "204",
            "inventory_slots": [],
            "richard_cutscene_confirmed": True,
        },
    )
    assert result["step_success"] is True
    assert q.pending_capture_indices == []


def test_finish_capture_flushes_every_pending_pl(monkeypatch, tmp_path):
    captured: list[int] = []

    def _fake_capture(env, state, breakdown, **kwargs):
        captured.append(int(kwargs.get("completed_index")))
        return {
            "source": "planner_loyal",
            "completed_index": kwargs.get("completed_index"),
        }

    monkeypatch.setattr(
        "re1_rl.planner_loyal_cells.capture_planner_loyal_cell", _fake_capture
    )
    q = PlannerLoyalQueue()
    q._index = 3
    q.pending_capture_indices = [0, 1, 2]
    progress = ProgressTracker(leg_span=1)
    progress.checkpoint_success = True
    env = SimpleNamespace(
        project_root=str(tmp_path),
        _planner_loyal_queue=q,
        _progress=progress,
        _checkpoint_freeze_pending=True,
        _checkpoint_captured=False,
        _macro_active=True,
        _yawn_rails_capture_pending=None,
        _apply_yawn_capture_ineligibility_penalty=lambda _bd: None,
        _planner_loyal_last_success=None,
    )
    env._maybe_capture_planner_loyal_cell = (
        lambda state, breakdown: RE1Env._maybe_capture_planner_loyal_cell(
            env, state, breakdown
        )
    )
    RE1Env._finish_checkpoint_capture(
        env, {"room_id": "101"}, {"checkpoint_success": 8.0}
    )
    assert captured == [0, 1, 2]
    assert q.pending_capture_indices == []
    assert len(env._yawn_rails_capture_pending) == 3


def test_combat_extractor_fuses_planner_steps_without_changing_features_dim():
    from gymnasium import spaces

    from re1_rl.combat_efficient_extractor import (
        FEATURES_DIM,
        RE1CombatEfficientExtractor,
        TOWER_OUT_DIM_PLANNER,
    )
    from re1_rl.planner_loyal import PLANNER_LOYAL_OMIT_OBS_KEYS
    from tests.test_doc04_medium_extractor import _stub_obs_space

    base = _stub_obs_space(with_world_state=True)
    spaces_map = dict(base.spaces)
    for key in PLANNER_LOYAL_OMIT_OBS_KEYS:
        spaces_map.pop(key, None)
    spaces_map["planner_steps"] = spaces.Box(
        -1.0, 1.0, shape=(PLANNER_QUEUE_DIM,), dtype=np.float32
    )
    obs_space = spaces.Dict(spaces_map)
    extractor = RE1CombatEfficientExtractor(obs_space, project_root=PROJECT_ROOT)
    assert extractor.planner_steps_proj is not None
    assert extractor.history_encoder is None
    assert extractor.world_context is None
    assert extractor._history_enabled is False
    assert extractor._world_enabled is False
    assert extractor._tower_out_dim == TOWER_OUT_DIM_PLANNER
    assert extractor.features_dim == FEATURES_DIM
    plain = RE1CombatEfficientExtractor(base, project_root=PROJECT_ROOT)
    assert plain.planner_steps_proj is None
    assert plain.history_encoder is not None
    assert plain.world_context is not None
    assert plain.features_dim == FEATURES_DIM


def test_planner_loyal_scalar_allowlist_exact_eight_and_minus_four():
    from re1_rl.planner_loyal import (
        PLANNER_LOYAL_SCALAR_KEYS,
        PLANNER_LOYAL_TELEMETRY_KEYS,
        scalarize_planner_loyal_reward,
    )
    from re1_rl.reward import scalarize_reward

    q = PlannerLoyalQueue()
    progress = ProgressTracker(leg_span=1)
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {"room_id": "105", "inventory_slots": [], "hp": 96, "in_control": True}
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert set(bd.keys()) <= PLANNER_LOYAL_SCALAR_KEYS | PLANNER_LOYAL_TELEMETRY_KEYS
    assert "new_room" not in bd
    assert reward == scalarize_planner_loyal_reward(bd)
    assert reward == scalarize_reward(bd, planner_loyal=True)
    assert reward == pytest.approx(STEP_PENALTY + PLANNER_STEP_SUCCESS_REWARD)

    q2 = PlannerLoyalQueue()
    progress2 = ProgressTracker()
    prev2 = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur2 = {"room_id": "107", "inventory_slots": [], "hp": 96, "in_control": True}
    reward2, bd2 = _reward(prev2, cur2, q2, progress=progress2)
    assert reward2 == pytest.approx(STEP_PENALTY + PLANNER_DIVERT_PENALTY)
    assert bd2["wrong_room"] == PLANNER_DIVERT_PENALTY
    assert reward2 == scalarize_planner_loyal_reward(bd2)


def test_planner_loyal_no_legacy_side_effect_keys():
    q = PlannerLoyalQueue()
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": False}
    cur = {
        "room_id": "106",
        "inventory_slots": [],
        "hp": 96,
        "in_control": False,
        "cutscene_key": "104:kenneth",
        "new_items": [],
    }
    _reward_total, bd = _reward(prev, cur, q)
    for legacy in (
        "new_room",
        "new_cutscene",
        "document_examine",
        "key_item",
        "story_use",
        "gallery",
        "item",
        "ammo_pickup",
    ):
        assert legacy not in bd


def test_planner_loyal_timeout_pays_minus_four():
    from re1_rl.yawn_cell_timeout import FLAT_CELL_TIMEOUT_FRAMES

    q = PlannerLoyalQueue()
    progress = ProgressTracker(leg_span=1)
    progress.arm_cell_timeout(100)
    progress.note_leg_frames(100)
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {
        "room_id": "106",
        "inventory_slots": [],
        "hp": 96,
        "in_control": True,
        "step_emulated_frames": 8,
    }
    reward, bd = _reward(prev, cur, q, progress=progress)
    assert bd["planner_timeout"] == PLANNER_DIVERT_PENALTY
    assert progress.cell_timeout_breached is True
    assert reward == pytest.approx(STEP_PENALTY + PLANNER_DIVERT_PENALTY)


def test_planner_loyal_stagnation_advances_during_cutscene():
    q = PlannerLoyalQueue()
    progress = ProgressTracker()
    prev = {"room_id": "106", "inventory_slots": [], "hp": 96, "in_control": True}
    cur = {
        "room_id": "106",
        "inventory_slots": [],
        "hp": 96,
        "in_control": False,
        "step_emulated_frames": 8,
    }
    _reward(prev, cur, q, progress=progress)
    assert progress.stagnation_frames == 8


def test_encode_planner_loyal_goal_has_compass_for_traverse():
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.planner_loyal import encode_planner_loyal_goal
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    state = {
        "room_id": "106",
        "x": 1000,
        "z": 2000,
        "facing": 0,
        "inventory": [],
    }
    goal = encode_planner_loyal_goal(
        encoder,
        graph,
        state,
        q,
        cell_time_remaining=1.0,
        item_positions=ItemPositions(PROJECT_ROOT / "data" / "item_positions.json"),
    )
    assert goal[21] > 0.0  # compass valid
    assert np.any(np.abs(goal[5:10]) > 0.01)
    assert goal[10] > 0.0  # obj_navigate


def test_encode_planner_loyal_goal_music_notes_compass():
    from re1_rl.obs_encoder import GOAL_BASE_DIM, ObsEncoder
    from re1_rl.planner_loyal import encode_planner_loyal_goal
    from re1_rl.room_graph import RoomGraph
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    q = PlannerLoyalQueue()
    q.seek(5)
    state = {
        "room_id": "10F",
        "x": 9000,
        "z": 8000,
        "facing": 0,
        "inventory": [],
    }
    goal = encode_planner_loyal_goal(
        encoder,
        graph,
        state,
        q,
        item_positions=ItemPositions(PROJECT_ROOT / "data" / "item_positions.json"),
    )
    assert goal[4] > 0.0  # in target room
    assert goal[11] > 0.0  # obj_pickup for acquire step
    assert goal[21] > 0.0
    assert goal[GOAL_BASE_DIM] > 0.0  # lookahead slot mask


def test_encode_planner_loyal_goal_star_crest_points_at_end_of_life():
    """pl47→48: compass + gallery hint aim at slot 8, not the RDT crest pile."""
    from re1_rl.obs_encoder import GOAL_FIELDS, ObsEncoder
    from re1_rl.planner_loyal import encode_planner_loyal_goal
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    positions = ItemPositions(PROJECT_ROOT / "data" / "item_positions.json")
    pile = positions.get("117", "star_crest")
    assert pile is not None
    assert pile != GALLERY_FINAL_SWITCH_TARGET

    q = PlannerLoyalQueue()
    crest_i = next(
        i
        for i, step in enumerate(q.remaining)
        if str(step.get("pickup_id") or "").startswith("117:star_crest")
    )
    q.seek(crest_i)
    assert q.current["beat_id"] == "star_crest"

    # pl47 spawn is the old man; hunting crest has not clicked death yet.
    state = {
        "room_id": "117",
        "x": GALLERY_TARGETS[5][0],
        "z": GALLERY_TARGETS[5][1],
        "facing": 0,
        "inventory": [],
        "gallery_progress": GALLERY_STEP_VALUES[-1],
        "gallery_puzzle_solved": False,
    }
    goal = encode_planner_loyal_goal(
        encoder, graph, state, q, item_positions=positions
    )
    gallery_i = next(
        i for i, (name, _desc) in enumerate(GOAL_FIELDS) if name == "gallery_bearing_sin"
    )
    eol = encoder._compass_to_xz(state, *GALLERY_FINAL_SWITCH_TARGET)
    pile_compass = encoder._compass_to_xz(state, pile[0], pile[1])
    assert np.allclose(goal[5:10], eol, atol=1e-5)
    assert not np.allclose(goal[5:10], pile_compass, atol=1e-3)
    assert np.allclose(goal[gallery_i : gallery_i + 4], encode_gallery_hint(state))
    assert goal[gallery_i + 2] > 0.5  # still a room away from slot 8
    assert goal[11] > 0.0  # obj_pickup

    solved = dict(state)
    solved["gallery_puzzle_solved"] = True
    after = encode_planner_loyal_goal(
        encoder, graph, solved, q, item_positions=positions
    )
    assert np.allclose(after[5:10], pile_compass, atol=1e-5)


def test_encode_planner_loyal_goal_end_of_life_is_use_item():
    """After the insert, pl47 hunts gallery_end_of_life as obj_use_item at slot 8."""
    from re1_rl.obs_encoder import ObsEncoder
    from re1_rl.planner_loyal import encode_planner_loyal_goal
    from re1_rl.spatial_encoder import ItemPositions

    graph = RoomGraph(PROJECT_ROOT / "data" / "doors_empirical.json")
    encoder = ObsEncoder(PROJECT_ROOT / "data" / "rooms.json", graph)
    positions = ItemPositions(PROJECT_ROOT / "data" / "item_positions.json")

    q = PlannerLoyalQueue()
    eol_i = next(
        i
        for i, step in enumerate(q.remaining)
        if step.get("beat_id") == "gallery_end_of_life"
    )
    q.seek(eol_i)
    assert q.current["site_id"] == "gallery_end_of_life"

    state = {
        "room_id": "117",
        "x": GALLERY_TARGETS[5][0],
        "z": GALLERY_TARGETS[5][1],
        "facing": 0,
        "inventory": [],
        "gallery_progress": GALLERY_STEP_VALUES[-1],
        "gallery_puzzle_solved": False,
    }
    goal = encode_planner_loyal_goal(
        encoder, graph, state, q, item_positions=positions
    )
    eol = encoder._compass_to_xz(state, *GALLERY_FINAL_SWITCH_TARGET)
    assert np.allclose(goal[5:10], eol, atol=1e-5)
    assert goal[12] > 0.0  # obj_use_item
    assert goal[11] == 0.0  # not obj_pickup


def test_validate_planner_loyal_stage_fail_closed():
    from re1_rl.planner_loyal import validate_planner_loyal_stage

    with pytest.raises(ValueError, match="mode=planner_loyal"):
        validate_planner_loyal_stage({"mode": "yawn_rails"})
    with pytest.raises(ValueError, match="route_steps"):
        validate_planner_loyal_stage(
            {"mode": "planner_loyal", "route_steps": [1, 2, 3]}
        )


def test_after_reward_step_skips_pb_and_go_explore(monkeypatch):
    pb_called = []
    ge_called = []

    monkeypatch.setattr(
        "re1_rl.pb_capture.pb_capture_enabled", lambda: True
    )
    monkeypatch.setattr(
        "re1_rl.go_explore_capture.go_explore_capture_enabled", lambda: True
    )

    env = SimpleNamespace(
        _planner_loyal_queue=PlannerLoyalQueue(),
        _arm_checkpoint_freeze=lambda: pb_called.append("freeze"),
        _queue_go_explore_progress=lambda *a, **k: ge_called.append(1),
        _maybe_capture_go_explore=lambda *a, **k: ge_called.append(2),
        _progress=SimpleNamespace(checkpoint_success=False),
        _pb_captured_triggers=set(),
        project_root=str(PROJECT_ROOT),
    )
    env._planner_loyal_active = lambda: True
    RE1Env._after_reward_step(
        env,
        {"room_id": "106"},
        {"room_id": "105"},
        {"planner_step_success": 8.0, "checkpoint_success": 8.0},
    )
    assert pb_called == ["freeze"]
    assert ge_called == []


def test_combat_targets_skip_frames_skipped_under_planner_loyal(monkeypatch):
    from re1_rl.combat_targets import pack_world_event_target_from_info

    monkeypatch.setenv("RE1_PLANNER_LOYAL", "1")
    y, _mask = pack_world_event_target_from_info(
        0,
        {"frames_skipped": 400, "reward_breakdown": {}},
    )
    assert y[3] == 0.0


def test_chunk_may_exceed_obs_window(tmp_path: Path):
    steps = [
        {"n": i + 1, "op": "traverse", "edge_id": f"10{i:X}->10{(i + 1):X}"}
        for i in range(PLANNER_MAX_STEPS + 3)
    ]
    chunk = {"chunk_id": "long", "end_anchor_beat_id": "tail", "steps": steps}
    validate_planner_loyal_chunk(chunk)
    q = PlannerLoyalQueue(chunk)
    assert len(q._steps) == PLANNER_MAX_STEPS + 3
    encoded = encode_planner_queue(q)
    assert len(encoded) == PLANNER_QUEUE_DIM
    q.seek(PLANNER_MAX_STEPS)
    assert q.current is not None
    assert not q.done


def test_pl18_seek_lands_on_chemical_tail():
    q = PlannerLoyalQueue()
    # pl18 completed shield_key (step index 12) → seek to 13.
    q.seek(13)
    assert q.current is not None
    assert q.current["edge_id"] == "105->106"
    assert q.end_anchor == "square_crank"
    assert q._steps[23]["pickup_id"].startswith("118:chemical")
    assert q._steps[24]["op"] == "use_box"
    assert any(
        str(s.get("pickup_id") or "").startswith("10C:armor_key") for s in q._steps
    )
    assert any(s.get("edge_id") == "101->100" for s in q._steps)
    assert any(s.get("edge_id") == "204->205" for s in q._steps)
    by_beat = {s.get("beat_id"): s for s in q._steps if s.get("beat_id")}
    assert by_beat["sun_crest"]["pickup_id"] == "205:sun_crest:1"
    assert by_beat["push_statue_2f"]["n"] == 94
    assert by_beat["wind_crest"]["n"] == 107
    assert by_beat["place_wind_crest"]["n"] == 129
    assert by_beat["place_moon_crest"]["n"] == 194
    assert by_beat["shed_push_stepladder"]["n"] == 196
    assert by_beat["square_crank"]["pickup_id"] == "11B:square_crank:1"
    assert by_beat["square_crank"]["n"] == 197
    assert "helmet_key" not in by_beat
    assert q._steps[-1]["beat_id"] == "square_crank"


def test_reload_if_stale_appends_new_steps(tmp_path: Path, monkeypatch):
    path = tmp_path / "chunk.json"
    path.write_text(
        json.dumps(
            {
                "chunk_id": "grow",
                "end_anchor_beat_id": "a",
                "steps": [{"n": 1, "op": "traverse", "edge_id": "105->106"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RE1_PLANNER_CHUNK", str(path))
    q = PlannerLoyalQueue(chunk_path=path)
    assert len(q._steps) == 1
    assert q.reload_if_stale(tmp_path) is False
    path.write_text(
        json.dumps(
            {
                "chunk_id": "grow",
                "end_anchor_beat_id": "b",
                "steps": [
                    {"n": 1, "op": "traverse", "edge_id": "105->106"},
                    {"n": 2, "op": "traverse", "edge_id": "106->107"},
                ],
            }
        ),
        encoding="utf-8",
    )
    # Content hash, not mtime — git pull must count even if timestamps match.
    os.utime(path, (q._chunk_path.stat().st_mtime, q._chunk_path.stat().st_mtime))
    assert q.reload_if_stale(tmp_path) is True
    assert len(q._steps) == 2
    assert q.end_anchor == "b"
    assert q.current["edge_id"] == "105->106"


def test_richard_bleedout_completes_on_confirmed_dump():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "richard",
            "end_anchor_beat_id": "leave_c",
            "steps": [
                {
                    "n": 1,
                    "op": "trigger_cutscene",
                    "site_id": "20D:richard",
                    "room_id": "20D",
                    "beat_id": "richard_bleedout",
                    "capture": False,
                },
                {"n": 2, "op": "traverse", "edge_id": "204->207"},
            ],
        }
    )
    progress = ProgressTracker()
    prev = {"room_id": "20D", "inventory": [], "inventory_slots": []}
    cur = {
        "room_id": "204",
        "inventory": [],
        "inventory_slots": [],
        "richard_cutscene_confirmed": True,
        "_skip_peak_scene_flag": 0x93,
    }
    result = q.evaluate_transition(prev_state=prev, state=cur, progress=progress)
    assert result["step_success"] is True
    assert result["divert"] is False
    assert q.current["edge_id"] == "204->207"
    # Dump already left Jill in 204 — next hop is 204->207, not auto-complete.
    stay = q.evaluate_transition(prev_state=cur, state=cur, progress=progress)
    assert stay["step_success"] is False
    assert not q.done


def test_richard_bleedout_diverts_without_confirmation():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "richard",
            "end_anchor_beat_id": "richard_bleedout",
            "steps": [
                {
                    "n": 1,
                    "op": "trigger_cutscene",
                    "site_id": "20D:richard",
                    "room_id": "20D",
                    "beat_id": "richard_bleedout",
                }
            ],
        }
    )
    result = q.evaluate_transition(
        prev_state={"room_id": "20D", "inventory": [], "inventory_slots": []},
        state={"room_id": "204", "inventory": [], "inventory_slots": []},
        progress=ProgressTracker(),
    )
    assert result["divert"] is True
    assert "unplanned_room" in str(result["divert_reason"])


def test_richard_floor_herb_diverts_on_cutscene_leg():
    """20D pillar herb is a floor pile, not a cutscene grant — divert."""
    q = PlannerLoyalQueue(
        {
            "chunk_id": "richard",
            "end_anchor_beat_id": "richard_bleedout",
            "steps": [
                {
                    "n": 1,
                    "op": "trigger_cutscene",
                    "site_id": "20D:richard",
                    "room_id": "20D",
                    "beat_id": "richard_bleedout",
                    "capture": False,
                }
            ],
        }
    )
    prev = {
        "room_id": "20D",
        "inventory": ["armor_key"],
        "inventory_slots": [{"item": "armor_key", "qty": 1}],
    }
    cur = {
        "room_id": "20D",
        "inventory": ["armor_key", "green_herb"],
        "inventory_slots": [
            {"item": "armor_key", "qty": 1},
            {"item": "green_herb", "qty": 1},
        ],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is True
    assert "green_herb" in str(result["divert_reason"])


def test_10c_third_green_herb_diverts_after_two_scripted():
    """Bench allows exactly 2 greens; a 3rd same-name pile is stuff / divert."""
    q = PlannerLoyalQueue()
    green_b = next(
        i for i, s in enumerate(q._steps) if s.get("pickup_id") == "10C:green_herb:2b"
    )
    q.seek(green_b + 1)  # past both greens; current is 10C->103
    assert q.current.get("edge_id") == "10C->103"
    prev = {
        "room_id": "10C",
        "inventory_slots": [
            ("beretta", 15),
            ("shotgun", 0),
            ("armor_key", 1),
            ("red_herb", 1),
            ("green_herb", 1),
            ("red_herb", 1),
            ("green_herb", 1),
            ("", 0),
        ],
    }
    cur = {
        "room_id": "10C",
        "inventory_slots": [
            ("beretta", 15),
            ("shotgun", 0),
            ("armor_key", 1),
            ("red_herb", 1),
            ("green_herb", 1),
            ("red_herb", 1),
            ("green_herb", 1),
            ("green_herb", 1),
        ],
    }
    result = q.evaluate_transition(prev_state=prev, state=cur)
    assert result["divert"] is True
    assert "green_herb" in str(result["divert_reason"])


def test_10c_rgrg_order_completes_each_pile():
    q = PlannerLoyalQueue()
    armor_i = next(
        i
        for i, s in enumerate(q._steps)
        if str(s.get("pickup_id") or "").startswith("10C:armor_key")
    )
    q.seek(armor_i + 1)
    held = [("beretta", 15), ("shotgun", 0), ("armor_key", 1)]
    for want in ("red_herb", "green_herb", "red_herb", "green_herb"):
        assert q.current.get("op") == "acquire"
        prev = {"room_id": "10C", "inventory_slots": list(held) + [("", 0)] * (8 - len(held))}
        held = held + [(want, 1)]
        cur = {"room_id": "10C", "inventory_slots": list(held) + [("", 0)] * (8 - len(held))}
        result = q.evaluate_transition(prev_state=prev, state=cur)
        assert result["step_success"] is True, (want, result)
        assert result["divert"] is False
    assert q.current.get("edge_id") == "10C->103"


def test_second_green_herb_diverts_when_already_holding_one():
    q = PlannerLoyalQueue()
    q.note_start_inventory(
        {"room_id": "106", "inventory_slots": [("green_herb", 1)]}
    )
    result = q.evaluate_transition(
        prev_state={
            "room_id": "106",
            "inventory_slots": [("green_herb", 1)],
        },
        state={
            "room_id": "106",
            "inventory_slots": [("green_herb", 1), ("green_herb", 1)],
            "new_items": ["green_herb"],
        },
    )
    assert result["divert"] is True
    assert "unplanned_pickup" in str(result["divert_reason"])


def test_dining_statue_completes_when_knocked():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "statue",
            "end_anchor_beat_id": "push_statue_2f",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "dining_statue_knocked",
                    "room_id": "202",
                    "beat_id": "push_statue_2f",
                }
            ],
        }
    )
    prev = {
        "room_id": "202",
        "dining_statue_knocked": False,
        "inventory": [],
        "inventory_slots": [],
    }
    cur = {
        "room_id": "202",
        "dining_statue_knocked": True,
        "inventory": [],
        "inventory_slots": [],
    }
    miss = q.evaluate_transition(prev_state=prev, state=prev)
    assert miss["step_success"] is False
    hit = q.evaluate_transition(prev_state=prev, state=cur)
    assert hit["step_success"] is True
    assert q.done


def test_dining_statue_completes_on_knock_even_with_room_change():
    """Yawn ``state_flag`` pays knock; unplanned_room must not block it."""
    q = PlannerLoyalQueue(
        {
            "chunk_id": "statue",
            "end_anchor_beat_id": "push_statue_2f",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "dining_statue_knocked",
                    "room_id": "202",
                    "beat_id": "push_statue_2f",
                }
            ],
        }
    )
    prev = {
        "room_id": "202",
        "dining_statue_knocked": False,
        "inventory": [],
        "inventory_slots": [],
    }
    cur = {
        "room_id": "201",
        "dining_statue_knocked": True,
        "inventory": [],
        "inventory_slots": [],
    }
    hit = q.evaluate_transition(prev_state=prev, state=cur)
    assert hit["step_success"] is True
    assert not hit["divert"]
    assert q.done


def test_dining_statue_completes_on_knock_despite_benign_inventory_edge():
    q = PlannerLoyalQueue(
        {
            "chunk_id": "statue",
            "end_anchor_beat_id": "push_statue_2f",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "dining_statue_knocked",
                    "room_id": "202",
                    "beat_id": "push_statue_2f",
                }
            ],
        }
    )
    prev = {
        "room_id": "202",
        "dining_statue_knocked": False,
        "inventory": [],
        "inventory_slots": [],
    }
    cur = {
        "room_id": "202",
        "dining_statue_knocked": True,
        "inventory": ["green_herb"],
        "inventory_slots": [("green_herb", 1)],
        "new_items": ["green_herb"],
    }
    hit = q.evaluate_transition(prev_state=prev, state=cur)
    assert hit["step_success"] is True
    assert not hit["divert"]
    assert q.done


def test_richard_skip_settled_on_planner_loyal_leg():
    from re1_rl.richard_cutscene_checkpoint import (
        RICHARD_CUTSCENE_KEY,
        note_richard_cutscene_skip_settle,
        richard_cutscene_skip_settled,
    )

    q = PlannerLoyalQueue(
        {
            "chunk_id": "richard",
            "end_anchor_beat_id": "richard_bleedout",
            "steps": [
                {
                    "n": 1,
                    "op": "trigger_cutscene",
                    "site_id": "20D:richard",
                    "room_id": "20D",
                    "beat_id": "richard_bleedout",
                }
            ],
        }
    )
    entry = {"room_id": "20D", "scene_flag": 0x91}
    new = {"room_id": "20D", "scene_flag": 0x93}
    assert richard_cutscene_skip_settled(
        None,
        entry,
        new,
        skip_frames=2984,
        peak_scene_flag=0x93,
        planner_loyal_queue=q,
    )
    progress = ProgressTracker()
    note_richard_cutscene_skip_settle(
        None,
        progress,
        entry,
        new,
        skip_frames=2984,
        peak_scene_flag=0x93,
        planner_loyal_queue=q,
    )
    assert RICHARD_CUTSCENE_KEY in progress.observed_cutscenes
    result = q.evaluate_transition(
        prev_state=entry,
        state={**new, "richard_cutscene_confirmed": True},
        progress=progress,
    )
    assert result["step_success"] is True

def test_vjolt_mix_and_plant42_boss_complete():
    from re1_rl.planner_loyal import _plant_42_boss_complete, _vjolt_mix_complete

    assert _vjolt_mix_complete(
        {"beat_id": "vjolt_mix", "site_id": "vjolt_mix@409"},
        {"inventory_slots": [("v_jolt", 1)]},
    )
    assert not _vjolt_mix_complete(
        {"beat_id": "vjolt_mix", "site_id": "vjolt_mix@409"},
        {"inventory_slots": [("water", 1)]},
    )
    assert _plant_42_boss_complete(
        {"beat_id": "plant_42", "site_id": "40C:plant_42"},
        {"room_id": "40C", "inventory_slots": [("helmet_key", 1)]},
        {"room_id": "40C", "inventory_slots": []},
    )
    q = PlannerLoyalQueue(
        {
            "chunk_id": "t",
            "end_anchor_beat_id": "vjolt_mix",
            "steps": [
                {
                    "n": 1,
                    "op": "do_puzzle",
                    "site_id": "vjolt_mix@409",
                    "room_id": "409",
                    "beat_id": "vjolt_mix",
                }
            ],
        }
    )
    r = q.evaluate_transition(
        prev_state={"room_id": "409", "inventory_slots": [("n_p003", 1)]},
        state={"room_id": "409", "inventory_slots": [("v_jolt", 1)]},
    )
    assert r["step_success"] is True


def test_chem_combine_vjolt_path():
    from re1_rl.inventory_combine import plan_combine

    # water + umb_no2 -> n_p003
    inv = [(20, 1), (21, 1)] + [(0, 0)] * 6
    planned = plan_combine(inv, 0, 1)
    assert planned is not None
    new_inv, _dest, product = planned
    assert product == 26
    assert new_inv[0] == (26, 1)
    # n_p003 + umb_no13 -> v_jolt
    inv2 = [(26, 1), (24, 1)] + [(0, 0)] * 6
    planned2 = plan_combine(inv2, 0, 1)
    assert planned2 is not None
    assert planned2[2] == 27

def test_shed_push_stepladder_and_crank_already_held():
    from re1_rl.shed_stepladder_puzzle import (
        SHED_STEPLADDER_SEATED_XZ,
        shed_acquire_crank_already_held,
        shed_stepladder_push_done,
        shed_stepladder_step_complete,
    )

    push = {
        "op": "do_puzzle",
        "site_id": "shed_stepladder@11B",
        "room_id": "11B",
        "beat_id": "shed_push_stepladder",
    }
    assert not shed_stepladder_step_complete(
        push, {"room_id": "11B", "inventory_slots": [("shotgun", 5)]}
    )
    assert shed_stepladder_step_complete(
        push, {"room_id": "11B", "inventory_slots": [("square_crank", 1)]}
    )
    seated = {
        "room_id": "11B",
        "inventory_slots": [("shotgun", 5)],
        "shed_stepladder_x": SHED_STEPLADDER_SEATED_XZ[0],
        "shed_stepladder_z": SHED_STEPLADDER_SEATED_XZ[1],
        "shed_stepladder_x_b": SHED_STEPLADDER_SEATED_XZ[0],
        "shed_stepladder_z_b": SHED_STEPLADDER_SEATED_XZ[1],
    }
    assert shed_stepladder_push_done(seated)
    assert shed_stepladder_step_complete(push, seated)
    acq = {
        "op": "acquire",
        "room_id": "11B",
        "pickup_id": "11B:square_crank:1",
        "beat_id": "square_crank",
    }
    assert shed_acquire_crank_already_held(
        acq, {"room_id": "11B", "inventory_slots": [("square_crank", 1)]}
    )
    q = PlannerLoyalQueue(
        {
            "chunk_id": "t",
            "end_anchor_beat_id": "square_crank",
            "steps": [push, acq],
        }
    )
    r = q.evaluate_transition(
        prev_state={"room_id": "11B", "inventory_slots": []},
        state={"room_id": "11B", "inventory_slots": [("square_crank", 1)]},
    )
    assert r["step_success"] is True
    assert q.current["beat_id"] == "square_crank"
    r2 = q.evaluate_transition(
        prev_state={"room_id": "11B", "inventory_slots": [("square_crank", 1)]},
        state={"room_id": "11B", "inventory_slots": [("square_crank", 1)]},
    )
    assert r2["step_success"] is True
