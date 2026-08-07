"""Offline contract tests for the Yawn one-leg rails curriculum."""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from re1_rl.obs_encoder import (
    GOAL_BASE_DIM,
    GOAL_FIELDS,
    GOAL_LOOKAHEAD_SLOT_DIM,
    GOAL_LOOKAHEAD_SLOTS,
    ObsEncoder,
)
from re1_rl.item_box import BOX_ROOMS
from re1_rl.env import RE1Env
from re1_rl.planner import WaypointPlanner
from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    CHECKPOINT_MAX_STEPS_EXTENSION,
    RAILS_CHECKPOINT_REWARD,
    SOFTLOCK_EXTENSION_FRAMES,
    compute_reward,
)
from re1_rl.room_graph import RoomGraph, load_valid_rooms
from re1_rl.yawn_rails import (
    capture_successor_cell,
    sample_one_leg_options,
    successor_capacity,
    validate_manifest_cells,
    validate_route,
)

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "data" / "yawn_checkpoint_route.json"
ROOMS = ROOT / "data" / "rooms.json"
DOORS = ROOT / "data" / "doors_empirical.json"
DOORS_RDT = ROOT / "data" / "doors_rdt.json"
GOAL_IDX = {name: i for i, (name, _) in enumerate(GOAL_FIELDS)}
_ROUTE_ROWS = json.loads(ROUTE.read_text(encoding="utf-8"))
_ROUTE_INDEX = {
    str(row["checkpoint_id"]): i for i, row in enumerate(_ROUTE_ROWS)
}
_ROUTE_N = len(_ROUTE_ROWS)


def _graph() -> RoomGraph:
    return RoomGraph(
        DOORS,
        DOORS_RDT,
        valid_rooms=load_valid_rooms(ROOMS),
    )


def _idx(checkpoint_id: str) -> int:
    return int(_ROUTE_INDEX[checkpoint_id])


def _planner(start_index: int = 0) -> WaypointPlanner:
    return WaypointPlanner(
        ROUTE,
        route_steps=list(range(1, _ROUTE_N + 1)),
        start_index=start_index,
    )


def _settle(progress: ProgressTracker, room: str, steps: int = 30) -> None:
    """Accumulate in-control dwell required by cutscene/door settle gates."""
    for _ in range(int(steps)):
        progress.record_in_control_step(room, True)


def _state(room: str, *, inventory=(), new_items=(), x=0, z=0, **extra) -> dict:
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
        **extra,
    }


def test_route_is_legal_and_excludes_rejected_objectives() -> None:
    route = json.loads(ROUTE.read_text(encoding="utf-8"))
    curriculum = json.loads(
        (ROOT / "curriculum/yawn_rails_one_leg.json").read_text(encoding="utf-8")
    )
    assert validate_route(route, graph=_graph()) == []
    # Cells may be wiped between capture cycles; empty manifest is valid.
    assert validate_manifest_cells(ROOT, curriculum, require_contiguous_prefix=0) == []
    assert curriculum["max_steps"] == 5400  # 12 min at 8 frames/step and 60fps.
    text = json.dumps(route).lower()
    assert '"205"' not in text
    assert "serum" not in text
    assert any(cp["room_id"] == "20D" and "handgun_bullets" in cp["items_gained"] for cp in route)
    tea_return = next(cp for cp in route if cp["checkpoint_id"] == "ammo_104")
    assert tea_return["action_type"] == "navigate"
    assert tea_return["items_gained"] == []
    assert tea_return["success_condition"] == {
        "type": "room_enter",
        "room_id": "104",
    }
    # L Passage and later: enter-room is never bundled with pickup/use/fight work.
    enter_108 = next(cp for cp in route if cp["checkpoint_id"] == "l_passage_enter_108")
    ammo_108 = next(cp for cp in route if cp["checkpoint_id"] == "ammo_108")
    assert enter_108["seq"] + 1 == ammo_108["seq"]
    assert enter_108["action_type"] == "navigate"
    assert enter_108["items_gained"] == []
    assert enter_108["success_condition"] == {
        "type": "room_enter",
        "room_id": "108",
    }
    assert ammo_108["action_type"] == "pickup"
    assert ammo_108["items_gained"] == ["handgun_bullets"]
    assert ammo_108["success_condition"] == {
        "type": "acquired_item",
        "item": "handgun_bullets",
    }
    l_idx = next(
        i for i, cp in enumerate(route) if cp["checkpoint_id"] == "l_passage_enter_108"
    )

    def _cond_types(cond: object, acc: list[str] | None = None) -> list[str]:
        out = acc if acc is not None else []
        if not isinstance(cond, dict):
            return out
        ctype = str(cond.get("type") or "")
        if ctype:
            out.append(ctype)
        for sub in cond.get("conditions") or []:
            _cond_types(sub, out)
        return out

    for cp in route[l_idx:]:
        types = _cond_types(cp.get("success_condition"))
        has_enter = any(
            t in types for t in ("room_enter", "room_enter_from", "room_enter_any")
        )
        extras = [
            t
            for t in types
            if t
            not in (
                "all_of",
                "any_of",
                "room_enter",
                "room_enter_from",
                "room_enter_any",
            )
        ]
        assert not (has_enter and extras), (
            f"{cp['checkpoint_id']} still bundles enter with {extras}"
        )
    assert [(cp["room_id"], cp["checkpoint_id"]) for cp in route[-10:]] == [
        ("118", "yawn_box_enter_118"),
        ("118", "yawn_box_prep_118"),
        ("10B", "east_stairs_101_to_yawn"),
        ("207", "east_stairs_201_to_yawn"),
        ("204", "c_passage_204_to_yawn"),
        ("20D", "moon_hall_enter_20D"),
        ("20D", "ammo_20D"),
        ("20E", "attic_entry_20E"),
        ("210", "yawn_arena_enter_210"),
        ("210", "yawn_moon_210"),
    ]
    bar_ids = [cp["checkpoint_id"] for cp in route if cp["room_id"] == "10F"]
    assert bar_ids == [
        "bar_enter_10F",
        "music_notes_10F",
        "piano_music_notes_10F",
        "gold_emblem_10F",
        "place_emblem_10F",
    ]
    place = next(cp for cp in route if cp["checkpoint_id"] == "place_emblem_10F")
    assert place["action_type"] == "use_item"
    assert "emblem" in place["required_items"]
    assert "gold_emblem" in place["required_items"]
    place_cond = json.dumps(place["success_condition"])
    assert "emblem@10F_alcove" in place_cond
    assert "emblem@10F_wall" in place_cond
    assert '"lacks_item"' in place_cond
    gold = next(cp for cp in route if cp["checkpoint_id"] == "gold_emblem_10F")
    assert gold["seq"] < place["seq"]
    assert "gold_emblem" in gold["items_gained"]
    place_gold = next(cp for cp in route if cp["checkpoint_id"] == "place_gold_emblem_105")
    place_gold_cond = json.dumps(place_gold["success_condition"])
    assert "gold_emblem@105_fireplace" in place_gold_cond
    assert '"lacks_item"' in place_gold_cond
    assert '"item": "gold_emblem"' in place_gold_cond
    crest = next(cp for cp in route if cp["checkpoint_id"] == "crest_gate_11A")
    crest_cond = json.dumps(crest["success_condition"])
    for item in ("star_crest", "sun_crest", "moon_crest", "wind_crest"):
        assert f'"item": "{item}"' in crest_cond
        assert f"{item}@11A_crest_slot" in crest_cond
    assert '"any_of"' in crest_cond
    assert crest_cond.count('"story_use"') == 4
    post_crest = next(
        cp for cp in route if cp["checkpoint_id"] == "back_passage_post_crest_10A"
    )
    east = next(cp for cp in route if cp["checkpoint_id"] == "east_stairs_101")
    assert crest["seq"] + 1 == post_crest["seq"]
    assert post_crest["seq"] + 1 == east["seq"]
    assert post_crest["room_id"] == "10A"
    chemical = next(cp for cp in route if cp["checkpoint_id"] == "chemical_118")
    stairs_101_post = next(
        cp for cp in route if cp["checkpoint_id"] == "east_stairs_101_post_storeroom"
    )
    stairs_201 = next(cp for cp in route if cp["checkpoint_id"] == "east_stairs_201")
    assert chemical["seq"] + 1 == stairs_101_post["seq"]
    assert stairs_101_post["seq"] + 1 == stairs_201["seq"]
    bazooka = next(cp for cp in route if cp["checkpoint_id"] == "bazooka_212")
    terrace_ret = next(cp for cp in route if cp["checkpoint_id"] == "terrace_return_211")
    upper_ret = next(cp for cp in route if cp["checkpoint_id"] == "upper_hall_203_post_terrace")
    dining_2f = next(cp for cp in route if cp["checkpoint_id"] == "dining_2f_enter_202")
    assert bazooka["seq"] + 1 == terrace_ret["seq"]
    assert terrace_ret["seq"] + 1 == upper_ret["seq"]
    assert upper_ret["seq"] + 1 == dining_2f["seq"]
    save_100 = next(cp for cp in route if cp["checkpoint_id"] == "save_100")
    west_ret = next(cp for cp in route if cp["checkpoint_id"] == "west_stairs_return_10B")
    corridor = next(cp for cp in route if cp["checkpoint_id"] == "central_corridor_103")
    assert save_100["seq"] + 1 == west_ret["seq"]
    assert west_ret["seq"] + 1 == corridor["seq"]
    armor = next(cp for cp in route if cp["checkpoint_id"] == "armor_key_10C")
    corridor_post = next(
        cp for cp in route if cp["checkpoint_id"] == "central_corridor_post_armor_103"
    )
    plant = next(cp for cp in route if cp["checkpoint_id"] == "plant_42_enter_10E")
    assert armor["seq"] + 1 == corridor_post["seq"]
    assert corridor_post["seq"] + 1 == plant["seq"]
    ammo_10e = next(cp for cp in route if cp["checkpoint_id"] == "ammo_10E")
    corridor_10e = next(
        cp for cp in route if cp["checkpoint_id"] == "central_corridor_post_10E_103"
    )
    tea_10e = next(cp for cp in route if cp["checkpoint_id"] == "tea_transit_104_post_10E")
    dining_jewel = next(cp for cp in route if cp["checkpoint_id"] == "dining_enter_105_jewel")
    assert ammo_10e["seq"] + 1 == corridor_10e["seq"]
    assert corridor_10e["seq"] + 1 == tea_10e["seq"]
    assert tea_10e["seq"] + 1 == dining_jewel["seq"]
    blue_jewel = next(cp for cp in route if cp["checkpoint_id"] == "blue_jewel_105")
    tea_jewel = next(cp for cp in route if cp["checkpoint_id"] == "tea_return_104_post_jewel")
    corridor_jewel = next(
        cp for cp in route if cp["checkpoint_id"] == "central_corridor_post_jewel_103"
    )
    forest = next(cp for cp in route if cp["checkpoint_id"] == "forest_enter_10D")
    assert blue_jewel["seq"] + 1 == tea_jewel["seq"]
    assert tea_jewel["seq"] + 1 == corridor_jewel["seq"]
    assert corridor_jewel["seq"] + 1 == forest["seq"]
    stairs_201_yawn = next(cp for cp in route if cp["checkpoint_id"] == "east_stairs_201_to_yawn")
    cpass_yawn = next(cp for cp in route if cp["checkpoint_id"] == "c_passage_204_to_yawn")
    moon = next(cp for cp in route if cp["checkpoint_id"] == "moon_hall_enter_20D")
    assert stairs_201_yawn["seq"] + 1 == cpass_yawn["seq"]
    assert cpass_yawn["seq"] + 1 == moon["seq"]
    assert curriculum["route_steps"][-1] == len(route)
    for checkpoint in route:
        condition_text = json.dumps(checkpoint["success_condition"])
        for item in checkpoint["items_gained"]:
            assert f'"item": "{item}"' in condition_text


def test_zero_coordinate_rdt_rows_are_not_walkable_edges() -> None:
    graph = _graph()
    assert graph.get_exit("20E", "100") is None
    assert graph.get_exit("20E", "210") is not None


def test_rails_nav_crumbs_keep_full_exploration_magnitudes() -> None:
    # On-path tea-room entry (kenneth leg) still pays full new_room under rails.
    # Off-target leaves are terminal wrong_room now — do not use those for crumb checks.
    planner = _planner(start_index=_idx("kenneth_104"))
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    _, bd = compute_reward(
        _state("105", inventory=["emblem"]),
        _state("104", inventory=["emblem"]),
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["new_room"] == pytest.approx(4.0)
    assert bd["wrong_room"] == 0.0
    assert bd["checkpoint_success"] == 0.0  # needs cutscene + settle


def test_checkpoint_key_item_already_held_pays_terminal_reward() -> None:
    planner = _planner()
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    # Emblem is a key item: inventory alone satisfies acquired_item.
    held_only = _state("105", inventory=["emblem"])
    reward, hit = compute_reward(
        _state("105"),
        held_only,
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert hit["checkpoint_success"] == pytest.approx(RAILS_CHECKPOINT_REWARD)
    assert progress.checkpoint_success
    assert max(v for k, v in hit.items() if k != "checkpoint_success") < RAILS_CHECKPOINT_REWARD
    assert reward > RAILS_CHECKPOINT_REWARD - 0.01


def test_barry_hall_return_is_203_to_106_path_only() -> None:
    """cp05: main-hall needs 106: settle; return from 203 needs control dwell."""
    first_entry = _planner(start_index=3)
    hall = ProgressTracker()
    hall.observe_cutscene("106:1:s0")
    _settle(hall, "106", steps=45)
    assert first_entry.advance_if_success(_state("106"), progress=hall)

    return_entry = _planner(start_index=5)
    ret = ProgressTracker()
    _settle(ret, "106")
    assert return_entry.advance_if_success(
        _state("106"),
        progress=ret,
        prev_state=_state("203"),
    )

    wrong_return = _planner(start_index=5)
    bad = ProgressTracker()
    _settle(bad, "106")
    assert not wrong_return.advance_if_success(
        _state("106"),
        progress=bad,
        prev_state=_state("201"),
    )


def test_barry_hall_return_pays_checkpoint_on_stairs_down() -> None:
    planner = _planner(start_index=5)
    progress = ProgressTracker()
    progress.seed_spawn_room("203")
    # Kenneth already paid so 203→106 is not the illegal pre-Kenneth hall gate.
    progress.observed_cutscenes.add("104:0:s0")
    _settle(progress, "106")
    reward, bd = compute_reward(
        _state("203"),
        _state("106"),
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] == pytest.approx(RAILS_CHECKPOINT_REWARD)
    assert planner.waypoint_index == 6
    assert progress.checkpoint_success
    assert progress.softlock_cap_frames == SOFTLOCK_EXTENSION_FRAMES
    assert progress.max_steps_bonus == CHECKPOINT_MAX_STEPS_EXTENSION
    assert reward > RAILS_CHECKPOINT_REWARD - 0.01


def test_barry_hall_return_latches_203_entry_same_leg() -> None:
    planner = _planner(start_index=5)
    progress = ProgressTracker()
    _settle(progress, "106")

    assert planner.advance_if_success(
        _state("106"),
        progress=progress,
        prev_state=_state("203"),
    )
    # After advance, transitions clear so a later same-room step does not re-fire.
    progress.on_waypoint_advanced()
    assert not progress.leg_room_transitions


def test_barry_hall_return_does_not_latch_wrong_from_room() -> None:
    planner = _planner(start_index=5)
    progress = ProgressTracker()
    _settle(progress, "106")

    assert not planner.advance_if_success(
        _state("106"),
        progress=progress,
        prev_state=_state("201"),
    )
    assert not planner.advance_if_success(
        _state("106"),
        progress=progress,
        prev_state=_state("106"),
    )


def test_crest_gate_requires_story_use_not_unheld_crest_lacks() -> None:
    """Sun/moon/wind lacks_item alone must not complete crest_gate while star is held."""
    planner = _planner(start_index=_idx("crest_gate_11A"))
    progress = ProgressTracker()
    progress.key_items_rewarded.add("star_crest")
    holding = _state(
        "11A",
        inventory=[
            "knife",
            "beretta",
            "shield_key",
            "shotgun",
            "star_crest",
        ],
    )
    assert not planner.advance_if_success(holding, progress=progress)

    placed = _state(
        "11A",
        inventory=["knife", "beretta", "shield_key", "shotgun"],
    )
    progress.rewarded_story_uses.add("star_crest@11A_crest_slot")
    assert planner.advance_if_success(placed, progress=progress)


def test_cp39_leg_east_stairs_101_room_enter() -> None:
    """cp39 reset (index 39) -> leg 40 is east_stairs_101; entering 10B completes."""
    planner = _planner(start_index=_idx("back_passage_post_crest_10A") + 1)
    assert planner.current_objective()["checkpoint_id"] == "east_stairs_101"
    progress = ProgressTracker()
    assert not planner.advance_if_success(_state("10A"), progress=progress)
    assert not planner.advance_if_success(_state("101"), progress=progress)
    assert planner.advance_if_success(_state("10B"), progress=progress)


def test_kenneth_requires_cutscene_and_in_control_settle() -> None:
    planner = _planner(start_index=_idx("kenneth_104"))
    bare = ProgressTracker()
    assert not planner.advance_if_success(_state("104"), progress=bare)

    mid = ProgressTracker()
    mid.observe_cutscene("104:0:s0")
    assert not planner.advance_if_success(_state("104"), progress=mid)

    ok = ProgressTracker()
    ok.observe_cutscene("104:0:s0")
    _settle(ok, "104", steps=45)
    assert planner.advance_if_success(_state("104"), progress=ok)


def test_pre_cutscene_room_dwell_does_not_satisfy_post_cutscene_settle() -> None:
    """Barry/Kenneth success must settle AFTER the cinema, not from walk-up dwell."""
    planner = _planner(start_index=_idx("barry_return_105"))
    progress = ProgressTracker()
    progress.note_leg_room_transition("104", "105")
    _settle(progress, "105", steps=90)  # walk-up before Barry return beat
    progress.observe_cutscene("105:2:s1")
    assert not planner.advance_if_success(
        _state("105"), progress=progress, prev_state=_state("104")
    )
    _settle(progress, "105", steps=60)
    assert planner.advance_if_success(
        _state("105"), progress=progress, prev_state=_state("104")
    )


def test_upper_hall_203_is_room_enter_settle_without_cutscene() -> None:
    """First climb to 203 has no cinema; cutscene gate would softlock cp04 forever."""
    planner = _planner(start_index=_idx("upper_hall_203"))
    bare = ProgressTracker()
    assert not planner.advance_if_success(_state("203"), progress=bare)
    ok = ProgressTracker()
    _settle(ok, "203", steps=30)
    assert planner.advance_if_success(_state("203"), progress=ok)


def test_main_hall_rejects_room_spoof_without_106_cutscene() -> None:
    planner = _planner(start_index=_idx("main_hall_106"))
    spoof = ProgressTracker()
    _settle(spoof, "106", steps=45)
    assert not planner.advance_if_success(_state("106"), progress=spoof)

    ok = ProgressTracker()
    ok.observe_cutscene("106:1:s0")
    _settle(ok, "106", steps=45)
    assert planner.advance_if_success(_state("106"), progress=ok)


def test_l_passage_enter_then_ammo_are_separate_legs() -> None:
    enter = _planner(start_index=_idx("l_passage_enter_108"))
    assert enter.current_objective()["checkpoint_id"] == "l_passage_enter_108"
    # Entering 108 alone completes the doorway cell (no ammo required).
    assert enter.advance_if_success(_state("108"), progress=ProgressTracker())
    assert enter.current_objective()["checkpoint_id"] == "ammo_108"

    ammo = _planner(start_index=_idx("ammo_108"))
    progress = ProgressTracker()
    # Already in the L Passage: pickup completes without re-checking the door.
    assert not ammo.advance_if_success(_state("108"), progress=progress)
    progress.note_leg_acquired("handgun_bullets")
    assert ammo.advance_if_success(
        _state("108", inventory=["handgun_bullets"]),
        progress=progress,
    )


def test_gallery_portrait_steps_are_separate_legs() -> None:
    from re1_rl.gallery_puzzle import GALLERY_STEP_VALUES

    progress = ProgressTracker()
    planner = _planner(start_index=_idx("gallery_portrait_1_117"))
    for step in range(6):
        cid = f"gallery_portrait_{step + 1}_117"
        assert planner.current_objective()["checkpoint_id"] == cid
        if step > 0:
            assert not planner.advance_if_success(
                _state("117", gallery_progress=GALLERY_STEP_VALUES[step - 1]),
                progress=progress,
            )
        assert planner.advance_if_success(
            _state("117", gallery_progress=GALLERY_STEP_VALUES[step]),
            progress=progress,
        )

    assert planner.current_objective()["checkpoint_id"] == "gallery_complete_117"
    assert not planner.advance_if_success(
        _state("117", gallery_progress=GALLERY_STEP_VALUES[5]),
        progress=progress,
    )
    assert planner.advance_if_success(
        _state("117", gallery_progress=0, gallery_puzzle_solved=True),
        progress=progress,
    )

    assert planner.current_objective()["checkpoint_id"] == "star_crest_117"
    progress.note_leg_acquired("star_crest")
    assert planner.advance_if_success(
        _state("117", gallery_progress=0, gallery_puzzle_solved=True, inventory=["star_crest"]),
        progress=progress,
    )
    assert planner.current_objective()["checkpoint_id"] == "back_passage_return_10A"


def test_main_hall_ink_checkpoint_is_room_enter_only() -> None:
    planner = _planner(start_index=_idx("ink_106"))
    assert planner.advance_if_success(_state("106"), progress=ProgressTracker())


def test_save_100_checkpoint_is_room_enter_only() -> None:
    planner = _planner(start_index=_idx("save_100"))
    assert planner.advance_if_success(_state("100"), progress=ProgressTracker())


def test_shotgun_rescue_requires_reentry_shotgun_and_ceiling_cutscene() -> None:
    prev = _state("116", inventory=["shotgun"])
    state = _state("115", inventory=["shotgun"])

    reenter = _planner(start_index=_idx("barry_reenter_115"))
    assert reenter.current_objective()["checkpoint_id"] == "barry_reenter_115"
    assert reenter.advance_if_success(
        state,
        progress=ProgressTracker(),
        prev_state=prev,
    )
    assert reenter.current_objective()["checkpoint_id"] == "barry_rescue_115"

    no_cutscene = _planner(start_index=_idx("barry_rescue_115"))
    assert not no_cutscene.advance_if_success(
        state,
        progress=ProgressTracker(),
        prev_state=prev,
    )

    no_shotgun = _planner(start_index=_idx("barry_rescue_115"))
    observed = ProgressTracker()
    observed.observe_cutscene("115:ceiling_lowering")
    assert not no_shotgun.advance_if_success(
        _state("115"),
        progress=observed,
        prev_state=prev,
    )

    rescued = _planner(start_index=_idx("barry_rescue_115"))
    assert rescued.advance_if_success(
        state,
        progress=observed,
        prev_state=prev,
    )


def test_goal_encodes_selected_one_leg_checkpoint() -> None:
    graph = _graph()
    encoder = ObsEncoder(ROOMS, graph, curriculum_stage_index=1)
    planner = _planner(start_index=_idx("attic_entry_20E"))
    state = _state("20D", x=1000, z=1000)
    goal = encoder.encode_goal(state, planner)
    assert planner.next_waypoint_room() == "20E"
    assert goal[GOAL_IDX["goal_room_index"]] == encoder._room_idx_norm("20E")
    assert goal[GOAL_IDX["doors_available"]] == 1.0
    remaining = _ROUTE_N - _idx("attic_entry_20E")
    assert goal[GOAL_IDX["waypoints_remaining"]] == pytest.approx(
        remaining / _ROUTE_N
    )


def test_goal_appends_six_masked_checkpoint_semantic_slots() -> None:
    encoder = ObsEncoder(ROOMS, _graph(), curriculum_stage_index=1)
    # Lookahead from east stairs after Richard: … ammo_20D, attic, yawn enter, yawn.
    planner = _planner(start_index=_idx("east_stairs_101_to_yawn"))
    goal = encoder.encode_goal(
        _state("10B", inventory=["shield_key", "shotgun"]),
        planner,
    )
    slots = goal[GOAL_BASE_DIM:].reshape(
        GOAL_LOOKAHEAD_SLOTS, GOAL_LOOKAHEAD_SLOT_DIM
    )
    assert slots[0, 0] == 1.0
    assert slots[0, 1] == encoder._room_idx_norm("10B")
    pickup_slots = [i for i in range(GOAL_LOOKAHEAD_SLOTS) if slots[i, 3 + 1] == 1.0]
    assert pickup_slots, "expected a pickup in lookahead"
    assert any(
        planner.peek_objective(offset) is not None
        and planner.peek_objective(offset)["checkpoint_id"] == "yawn_moon_210"
        for offset in range(0, _ROUTE_N - _idx("east_stairs_101_to_yawn"))
    )


def test_two_leg_episode_pays_each_checkpoint_and_resets_acquisitions() -> None:
    planner = _planner()
    progress = ProgressTracker(leg_span=2)
    progress.seed_spawn_room("105")
    _, first = compute_reward(
        _state("105"),
        _state("105", inventory=["emblem"], new_items=["emblem"]),
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert first["checkpoint_success"] == pytest.approx(RAILS_CHECKPOINT_REWARD)
    assert progress.legs_completed == 1
    assert not progress.checkpoint_success
    assert progress.leg_acquired_items == set()
    assert planner.waypoint_index == 1

    progress.observe_cutscene("104:kenneth")
    _settle(progress, "104", steps=45)
    _, second = compute_reward(
        _state("105", inventory=["emblem"]),
        _state("104", inventory=["emblem"]),
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert second["checkpoint_success"] == pytest.approx(RAILS_CHECKPOINT_REWARD)
    assert progress.legs_completed == 2
    assert progress.checkpoint_success
    assert planner.waypoint_index == 2


def test_yawn_episode_terminates_only_after_configured_leg_span() -> None:
    progress = ProgressTracker(leg_span=2)
    env = SimpleNamespace(
        _progress=progress,
        _stage={"mode": "yawn_rails"},
        _episode_truncated=lambda: False,
    )
    progress.claim_checkpoint_success()
    assert RE1Env._termination_flags(env, _state("105")) == (False, False, None)
    progress.claim_checkpoint_success()
    assert RE1Env._termination_flags(env, _state("104")) == (
        True,
        False,
        "checkpoint_success",
    )


def test_yawn_box_prep_requires_natural_lab_timer_expiry() -> None:
    planner = _planner(start_index=_idx("yawn_box_prep_118"))
    assert planner.current_objective()["checkpoint_id"] == "yawn_box_prep_118"
    assert "118" in BOX_ROOMS

    active = _state("118")
    active["lab_timer"] = 1
    assert not planner.advance_if_success(active, progress=ProgressTracker())

    expired = _state("118")
    expired["lab_timer"] = 0
    assert planner.advance_if_success(expired, progress=ProgressTracker())


def test_richard_checkpoint_accepts_forced_settle_in_204() -> None:
    planner = _planner(start_index=_idx("richard_cutscene_20D"))
    progress = ProgressTracker()
    progress.observe_cutscene("20D:richard")
    settled = _state("204")
    assert planner.advance_if_success(settled, progress=progress)
    assert planner.current_objective()["checkpoint_id"] == "richard_forced_return_204"


def _write_cell(tmp_path: Path, idx: int) -> dict:
    cell = tmp_path / f"states/cp{idx}"
    cell.mkdir(parents=True, exist_ok=True)
    (cell / "cell.State").write_bytes(b"state")
    (cell / "cell.sidecar.json").write_text("{}", encoding="utf-8")
    return {
        "checkpoint_index": idx,
        "state_path": f"states/cp{idx}/cell.State",
        "sidecar_path": f"states/cp{idx}/cell.sidecar.json",
        "inventory_feasible": True,
        "inventory_free_slots": 4,
        "next_slots_needed": 0,
        "captured_in_box_room": False,
    }


def test_route_cell_sampling_is_seed_deterministic_and_never_archive(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, 18)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {"route_id": "test", "cells_manifest": "manifest.json"}
    a = sample_one_leg_options(tmp_path, stage, rng=random.Random(7))
    b = sample_one_leg_options(tmp_path, stage, rng=random.Random(7))
    assert a == b
    assert a["reset_source"] == "route_cell"
    assert a["reset_source"] not in {"pb", "archive", "route_initial"}
    assert a["leg_span"] == 1


def test_reset_mix_prefers_latest_cell_then_older_eligible(tmp_path: Path) -> None:
    # 3 eligible cells (18,19,20) → latest 20%; remaining 80% uniform over 18,19.
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 21)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 30)),
    }
    counts = {"latest": 0, "older": 0}
    per_start: dict[int, int] = {}
    for seed in range(3000):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        start = int(opts["route_start_index"])
        per_start[start] = per_start.get(start, 0) + 1
        if start == 21:  # latest cell index 20 → start 21
            counts["latest"] += 1
        else:
            counts["older"] += 1
    total = sum(counts.values())
    assert counts["latest"] / total == pytest.approx(0.20, abs=0.04)
    assert counts["older"] / total == pytest.approx(0.80, abs=0.04)
    assert per_start.get(19, 0) / total == pytest.approx(0.40, abs=0.05)
    assert per_start.get(20, 0) / total == pytest.approx(0.40, abs=0.05)


def test_reset_latest_only_env_pins_newest_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 21)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 30)),
    }
    monkeypatch.setenv("RE1_YAWN_RESET_LATEST_ONLY", "1")
    for seed in range(50):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["route_start_index"] == 21
        assert opts["reset_source"] == "route_cell"
        assert str(opts["pb_bundle"]["state_path"]).replace("\\", "/").endswith(
            "states/cp20/cell.State"
        )


def test_chaining_curriculum_samples_bounded_remaining_span(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, 18)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 30)),
        "legs_per_episode": 6,
    }
    opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(1))
    assert opts["route_start_index"] == 19
    assert opts["leg_span"] == 6
    chaining = json.loads(
        (ROOT / "curriculum/yawn_rails_chaining.json").read_text(encoding="utf-8")
    )
    one_leg = json.loads(
        (ROOT / "curriculum/yawn_rails_one_leg.json").read_text(encoding="utf-8")
    )
    assert chaining["legs_per_episode"] == 6
    assert chaining["episode_mode"] == "multi_leg"
    assert chaining["route_id"] == "yawn_quest_v2"
    assert chaining["route_steps"][-1] == _ROUTE_N
    assert one_leg["episode_mode"] == "one_leg"
    assert one_leg["route_id"] == "yawn_quest_v2"
    assert one_leg["route_steps"][-1] == _ROUTE_N
    assert "legs_per_episode" not in one_leg


def test_successor_capacity_uses_stack_headroom_and_consumption() -> None:
    full = [
        ("knife", 0),
        ("beretta", 15),
        ("first_aid_spray_alt", 1),
        ("emblem", 1),
        ("lockpick", 1),
        ("shotgun", 1),
        ("handgun_bullets", 59),
        ("chemical", 1),
    ]
    ammo = {"checkpoint_id": "ammo", "items_gained": ["handgun_bullets"]}
    accepted = successor_capacity(
        _state("20D") | {"inventory_slots": full},
        ammo,
    )
    assert accepted["inventory_feasible"] is True
    assert accepted["next_slots_needed"] == 0

    full[6] = ("handgun_bullets", 60)
    rejected = successor_capacity(
        _state("20D") | {"inventory_slots": full},
        ammo,
    )
    assert rejected["inventory_feasible"] is False
    assert rejected["next_slots_needed"] == 1

    swap = {
        "checkpoint_id": "armor",
        "items_gained": ["armor_key"],
        "consume_before_gain": ["chemical"],
    }
    consumed = successor_capacity(
        _state("10C") | {"inventory_slots": full},
        swap,
    )
    assert consumed["inventory_feasible"] is True
    assert consumed["inventory_free_slots"] == 0
    assert consumed["next_slots_needed"] == 1


def test_successor_capacity_always_allows_box_room() -> None:
    capacity = successor_capacity(
        _state("118") | {"inventory_slots": [("red_herb", 1)] * 8},
        {"checkpoint_id": "two", "items_gained": ["moon_crest", "shotgun_shells"]},
    )
    assert capacity["captured_in_box_room"] is True
    assert capacity["inventory_feasible"] is True


def test_infeasible_successor_is_rejected_before_savestate(tmp_path: Path) -> None:
    bridge = MagicMock()
    planner = SimpleNamespace(
        waypoint_index=1,
        total_waypoints=2,
        step_by_seq=lambda seq: (
            {"checkpoint_id": "pickup", "items_gained": ["moon_crest"]}
            if seq == 2
            else {"checkpoint_id": "done", "items_gained": []}
        ),
    )
    env = SimpleNamespace(
        project_root=tmp_path,
        _stage={"mode": "yawn_rails"},
        _planner=planner,
        bridge=bridge,
    )
    state = _state("20E") | {
        "inventory_slots": [
            ("knife", 0),
            ("beretta", 15),
            ("first_aid_spray_alt", 1),
            ("emblem", 1),
            ("lockpick", 1),
            ("shotgun", 1),
            ("handgun_bullets", 60),
            ("chemical", 1),
        ]
    }
    assert capture_successor_cell(
        env, state, {"checkpoint_success": RAILS_CHECKPOINT_REWARD}
    ) is None
    bridge.save_savestate.assert_not_called()


def test_sampling_filters_legacy_and_infeasible_mandatory_pickup_rows(
    tmp_path: Path,
) -> None:
    route = [
        {"checkpoint_id": "done", "items_gained": []},
        {"checkpoint_id": "pickup", "items_gained": ["emblem"]},
    ]
    (tmp_path / "route.json").write_text(json.dumps(route), encoding="utf-8")
    cells = []
    for idx, extra in enumerate((
        {},
        {
            "inventory_feasible": False,
            "inventory_free_slots": 0,
            "next_slots_needed": 1,
            "captured_in_box_room": False,
        },
    )):
        cp_idx = 18 + idx
        cell = tmp_path / f"states/cp{cp_idx}"
        cell.mkdir(parents=True)
        (cell / "cell.State").write_bytes(b"state")
        (cell / "cell.sidecar.json").write_text("{}", encoding="utf-8")
        cells.append({
            "checkpoint_index": cp_idx,
            "state_path": f"states/cp{cp_idx}/cell.State",
            "sidecar_path": f"states/cp{cp_idx}/cell.sidecar.json",
            **extra,
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "route_id": "test", "cells": cells}),
        encoding="utf-8",
    )
    stage = {
        "route_id": "test",
        "route_path": "route.json",
        "cells_manifest": "manifest.json",
    }
    chosen = sample_one_leg_options(tmp_path, stage, rng=random.Random(0))
    assert chosen["reset_source"] == "route_cell"


def test_checkpoint_success_proposes_without_local_install_when_sync_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "1")
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
        _macro_active=False,
        _read_state=lambda track_items=False: _state("105", inventory=["emblem"]),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )

    # completed index 0 = emblem_105; room must match route capture room.
    proposal = capture_successor_cell(
        env,
        _state("105", inventory=["emblem"]),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert env._macro_active is False  # restored after capture

    curated = tmp_path / "states/yawn_rails/cells/cp00/cell.State"
    assert proposal is not None
    assert proposal["checkpoint_index"] == 0
    assert proposal["next_checkpoint_id"] == "kenneth_104"
    assert not curated.is_file()  # learner + poll install curated slots
    assert bridge.save_savestate.called
    # Staging cleaned up after propose.
    staging_root = tmp_path / "states/yawn_rails/.staging"
    if staging_root.is_dir():
        assert list(staging_root.iterdir()) == []


def test_local_cas_installs_better_and_rejects_worse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    from re1_rl.yawn_rails_sync import (
        CELL_SIDECAR_NAME,
        CELL_STATE_NAME,
        try_install_yawn_cell,
        yawn_rails_root,
    )

    yr = yawn_rails_root(tmp_path)
    # Seed a strong curated cell.
    good = yr / "cells" / "cp00"
    good.mkdir(parents=True)
    (good / CELL_STATE_NAME).write_bytes(b"GOOD")
    (good / CELL_SIDECAR_NAME).write_text("{}", encoding="utf-8")
    (yr / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "archive_version": 1,
                "cells": [
                    {
                        "checkpoint_index": 0,
                        "checkpoint_id": "emblem_105",
                        "quality": [96, 45, 0, 4, 1, 0],
                        "state_path": "states/yawn_rails/cells/cp00/cell.State",
                        "sidecar_path": "states/yawn_rails/cells/cp00/cell.sidecar.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    worse = yr / ".staging" / "worse"
    worse.mkdir(parents=True)
    (worse / CELL_STATE_NAME).write_bytes(b"BAD")
    (worse / CELL_SIDECAR_NAME).write_text("{}", encoding="utf-8")
    assert not try_install_yawn_cell(
        tmp_path,
        checkpoint_index=0,
        staged_dir=worse,
        quality=[51, 45, 0, 4, 1, 0],
        row={"checkpoint_id": "emblem_105", "quality": [51, 45, 0, 4, 1, 0]},
    )
    assert (good / CELL_STATE_NAME).read_bytes() == b"GOOD"

    better = yr / ".staging" / "better"
    better.mkdir(parents=True)
    (better / CELL_STATE_NAME).write_bytes(b"BETTER")
    (better / CELL_SIDECAR_NAME).write_text("{}", encoding="utf-8")
    assert try_install_yawn_cell(
        tmp_path,
        checkpoint_index=0,
        staged_dir=better,
        quality=[96, 60, 0, 4, 1, 0],
        row={"checkpoint_id": "emblem_105", "quality": [96, 60, 0, 4, 1, 0]},
    )
    assert (yr / "cells" / "cp00" / CELL_STATE_NAME).read_bytes() == b"BETTER"
    man = json.loads((yr / "manifest.json").read_text(encoding="utf-8"))
    assert man["cells"][0]["quality"][:2] == [96, 60]


def test_story_progress_allows_overwrite_blocks_cutscene_regression() -> None:
    from re1_rl.yawn_rails_sync import story_progress_allows_overwrite

    old = {
        "progress": {"observed_cutscenes": ["104:0:s0", "105:2:s1"]},
        "captured_room_id": "105",
        "capture_step": 400,
        "episode_history": {"room_entries": [["105", 100]]},
    }
    thin = {
        "progress": {"observed_cutscenes": ["104:0:s0"]},
        "captured_room_id": "105",
        "capture_step": 120,
        "episode_history": {"room_entries": [["105", 100]]},
    }
    settled = {
        "progress": {
            "observed_cutscenes": ["104:0:s0", "105:2:s1", "105:2:s0"]
        },
        "captured_room_id": "105",
        "capture_step": 500,
        "episode_history": {"room_entries": [["105", 100]]},
    }
    assert not story_progress_allows_overwrite(thin, old, room_id="105")
    assert story_progress_allows_overwrite(settled, old, room_id="105")
    # Shorter dwell must not block pay-forward when cutscenes are preserved.
    fast = {
        "progress": {"observed_cutscenes": ["104:0:s0", "105:2:s1"]},
        "captured_room_id": "105",
        "capture_step": 50,
        "episode_history": {"room_entries": [["105", 0]]},
    }
    assert story_progress_allows_overwrite(fast, old, room_id="105")


def test_pay_forward_ammo_beats_despite_shorter_dwell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cp14-style pay-forward: more ammo at equal HP replaces incumbent."""
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    from re1_rl.yawn_rails_sync import (
        CELL_META_NAME,
        CELL_SIDECAR_NAME,
        CELL_STATE_NAME,
        try_install_yawn_cell,
        yawn_rails_root,
    )

    yr = yawn_rails_root(tmp_path)
    slot = yr / "cells" / "cp15"
    slot.mkdir(parents=True)
    (slot / CELL_STATE_NAME).write_bytes(b"OLD15")
    (slot / CELL_SIDECAR_NAME).write_text(
        json.dumps(
            {
                "progress": {
                    "observed_cutscenes": ["104:0:s0", "105:2:s1", "105:2:s0"]
                },
                "captured_room_id": "105",
                "capture_step": 1721,
                "episode_history": {"room_entries": [["105", 0], ["105", 1419]]},
            }
        ),
        encoding="utf-8",
    )
    (yr / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "archive_version": 1,
                "cells": [
                    {
                        "checkpoint_index": 15,
                        "checkpoint_id": "shield_key_105",
                        "quality": [96, 15, 1, 8, 1, 0],
                        "state_path": "states/yawn_rails/cells/cp15/cell.State",
                        "sidecar_path": (
                            "states/yawn_rails/cells/cp15/cell.sidecar.json"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    staged = yr / ".staging" / "cp15_better_ammo"
    staged.mkdir(parents=True)
    (staged / CELL_STATE_NAME).write_bytes(b"NEW15")
    (staged / CELL_SIDECAR_NAME).write_text(
        json.dumps(
            {
                "progress": {
                    "observed_cutscenes": ["104:0:s0", "105:2:s1", "105:2:s0"]
                },
                "captured_room_id": "105",
                "capture_step": 80,
                "episode_history": {"room_entries": [["105", 0]]},
            }
        ),
        encoding="utf-8",
    )
    assert try_install_yawn_cell(
        tmp_path,
        checkpoint_index=15,
        staged_dir=staged,
        quality=[96, 30, 1, 8, 1, 0],
        row={"checkpoint_id": "shield_key_105", "room_id": "105"},
    )
    assert (slot / CELL_STATE_NAME).read_bytes() == b"NEW15"
    man = json.loads((yr / "manifest.json").read_text(encoding="utf-8"))
    cp15 = next(c for c in man["cells"] if c["checkpoint_index"] == 15)
    assert cp15["quality"][:2] == [96, 30]


def test_local_cas_rejects_story_regress_despite_better_hp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    from re1_rl.yawn_rails_sync import (
        CELL_SIDECAR_NAME,
        CELL_STATE_NAME,
        try_install_yawn_cell,
        yawn_rails_root,
    )

    yr = yawn_rails_root(tmp_path)
    good = yr / "cells" / "cp02"
    good.mkdir(parents=True)
    (good / CELL_STATE_NAME).write_bytes(b"STORY")
    (good / CELL_SIDECAR_NAME).write_text(
        json.dumps(
            {
                "progress": {"observed_cutscenes": ["104:0:s0", "105:2:s1"]},
                "captured_room_id": "105",
                "capture_step": 400,
                "episode_history": {"room_entries": [["104", 10], ["105", 200]]},
            }
        ),
        encoding="utf-8",
    )
    (yr / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "archive_version": 1,
                "cells": [
                    {
                        "checkpoint_index": 2,
                        "checkpoint_id": "barry_return_105",
                        "quality": [80, 40, 0, 4, 1, 0],
                        "state_path": "states/yawn_rails/cells/cp02/cell.State",
                        "sidecar_path": (
                            "states/yawn_rails/cells/cp02/cell.sidecar.json"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    healthier = yr / ".staging" / "healthier_thin"
    healthier.mkdir(parents=True)
    (healthier / CELL_STATE_NAME).write_bytes(b"THIN")
    (healthier / CELL_SIDECAR_NAME).write_text(
        json.dumps(
            {
                "progress": {"observed_cutscenes": ["104:0:s0"]},
                "captured_room_id": "105",
                "capture_step": 210,
                "episode_history": {"room_entries": [["105", 200]]},
            }
        ),
        encoding="utf-8",
    )
    assert not try_install_yawn_cell(
        tmp_path,
        checkpoint_index=2,
        staged_dir=healthier,
        quality=[96, 60, 0, 4, 1, 0],
        row={"checkpoint_id": "barry_return_105", "room_id": "105"},
    )
    assert (good / CELL_STATE_NAME).read_bytes() == b"STORY"


def test_barry_return_capture_requires_105_2_s1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    from re1_rl.progress import ProgressTracker

    bridge = MagicMock()
    bridge.save_savestate.side_effect = (
        lambda path: Path(path).write_bytes(b"state")
    )
    planner = _planner(start_index=_idx("barry_return_105") + 1)
    progress = ProgressTracker()
    progress.observed_cutscenes.add("104:0:s0")  # Kenneth only — missing return beat
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
        _read_state=lambda track_items=False: _state("105"),
    )
    assert (
        capture_successor_cell(
            env, _state("105"), {"checkpoint_success": RAILS_CHECKPOINT_REWARD}
        )
        is None
    )
    bridge.save_savestate.assert_not_called()

    progress.observed_cutscenes.add("105:2:s1")
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {
            "schema_version": 1,
            "progress": {"observed_cutscenes": sorted(progress.observed_cutscenes)},
            "episode_history": {"room_entries": [["105", 100]]},
        },
    )
    proposal = capture_successor_cell(
        env, _state("105"), {"checkpoint_success": RAILS_CHECKPOINT_REWARD}
    )
    assert proposal is not None
    assert proposal["checkpoint_id"] == "barry_return_105"


def test_118_almanac_has_chemical_but_not_square_crank() -> None:
    room_items = json.loads((ROOT / "data/room_items.json").read_text(encoding="utf-8"))
    names = {row["name"] for row in room_items["118"]["items"]}
    assert "chemical" in names
    assert "square_crank" not in names


def test_11b_almanac_has_square_crank_not_mansion_storeroom_loot() -> None:
    room_items = json.loads((ROOT / "data" / "room_items.json").read_text(encoding="utf-8"))
    names = {row["name"] for row in room_items["11B"]["items"]}
    assert "square_crank" in names
    assert "chemical" not in names
