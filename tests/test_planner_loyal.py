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
    validate_planner_loyal_chunk,
)
from re1_rl.room_graph import RoomGraph
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    CHECKPOINT_MAX_STEPS_EXTENSION,
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
    assert steps[-1]["pickup_id"].startswith("10C:armor_key")
    assert steps[-1].get("beat_id") == "armor_key"
    assert any(s.get("edge_id") == "116->115" for s in steps)
    assert any(s.get("edge_id") == "115->109" for s in steps)
    assert any(s.get("edge_id") == "10A->117" for s in steps)
    assert not any(s.get("edge_id") == "116->106" for s in steps)
    assert any(s.get("edge_id") == "103->10C" for s in steps)
    assert not any(s.get("edge_id") == "103->104" for s in steps)
    portraits = [
        s.get("beat_id") for s in steps if str(s.get("beat_id") or "").startswith("gallery_portrait_")
    ]
    assert portraits == [f"gallery_portrait_{i}" for i in range(1, 7)]
    assert not any(s.get("site_id") == "gallery_end_of_life" for s in steps)
    crest_i = next(i for i, s in enumerate(steps) if str(s.get("pickup_id") or "").startswith("117:star_crest"))
    old_man = next(i for i, s in enumerate(steps) if s.get("beat_id") == "gallery_portrait_6")
    assert old_man < crest_i
    assert steps[old_man + 1]["pickup_id"].startswith("117:star_crest")
    assert steps[crest_i + 1]["edge_id"] == "117->10A"
    assert steps[crest_i + 2]["edge_id"] == "10A->11A"
    assert steps[crest_i + 3]["site_id"] == "star_crest@11A_crest_slot"
    pump_i = next(i for i, s in enumerate(steps) if s.get("site_id") == "chemical@10C_greenhouse_pump")
    herb_i = next(i for i, s in enumerate(steps) if s.get("pickup_id") == "10C:green_herb:2")
    armor_i = next(i for i, s in enumerate(steps) if str(s.get("pickup_id") or "").startswith("10C:armor_key"))
    assert pump_i < herb_i < armor_i
    assert steps[pump_i - 1]["edge_id"] == "103->10C"
    assert any(s.get("edge_id") == "106->107" for s in steps)
    assert "108:handgun_bullets:1" in pickups
    enter_108 = next(i for i, s in enumerate(steps) if s.get("edge_id") == "107->108")
    assert steps[enter_108 + 1]["pickup_id"] == "108:handgun_bullets:1"


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

    def _fake_capture(env, state, breakdown):
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
    assert q.end_anchor == "armor_key"
    assert q._steps[23]["pickup_id"].startswith("118:chemical")
    assert q._steps[24]["op"] == "use_box"
    assert q._steps[-1]["pickup_id"].startswith("10C:armor_key")


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
