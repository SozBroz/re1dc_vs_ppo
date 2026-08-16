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
    RAILS_CAPTURE_INELIGIBLE_PENALTY,
    RAILS_CHECKPOINT_REWARD,
    SOFTLOCK_EXTENSION_FRAMES,
    compute_reward,
)
from re1_rl.room_graph import RoomGraph, load_valid_rooms
from re1_rl.yawn_rails import (
    capture_successor_cell,
    iter_loadable_cells,
    sample_one_leg_options,
    successor_capacity,
    validate_manifest_cells,
    validate_route,
    yawn_capture_ineligible_reason,
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


def _settle(progress: ProgressTracker, room: str, steps: int = 2) -> None:
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
        "type": "all_of",
        "conditions": [
            {"type": "acquired_item", "item": "handgun_bullets"},
            {"type": "leg_kills_in_room", "room_id": "108", "min_kills": 2},
        ],
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
                "leg_kills_in_room",
            )
        ]
        assert not (has_enter and extras), (
            f"{cp['checkpoint_id']} still bundles enter with {extras}"
        )
    assert [(cp["room_id"], cp["checkpoint_id"]) for cp in route[-10:]] == [
        ("10B", "east_stairs_101_post_richard"),
        ("118", "yawn_box_enter_118"),
        ("118", "yawn_box_prep_118"),
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
    vacant_101 = next(cp for cp in route if cp["checkpoint_id"] == "vacant_detour_enter_101")
    vacant_102 = next(cp for cp in route if cp["checkpoint_id"] == "vacant_enter_102")
    vacant_ammo = next(cp for cp in route if cp["checkpoint_id"] == "vacant_ammo_102")
    vacant_ret_101 = next(cp for cp in route if cp["checkpoint_id"] == "vacant_return_101")
    vacant_ret_103 = next(cp for cp in route if cp["checkpoint_id"] == "vacant_return_103")
    plant = next(cp for cp in route if cp["checkpoint_id"] == "plant_42_enter_10E")
    assert armor["seq"] + 1 == corridor_post["seq"]
    assert corridor_post["seq"] + 1 == vacant_101["seq"]
    assert vacant_101["seq"] + 1 == vacant_102["seq"]
    assert vacant_102["seq"] + 1 == vacant_ammo["seq"]
    assert vacant_ammo["seq"] + 1 == vacant_ret_101["seq"]
    assert vacant_ret_101["seq"] + 1 == vacant_ret_103["seq"]
    assert vacant_ret_103["seq"] + 1 == plant["seq"]
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
    assert bd["new_room"] == pytest.approx(0.0)
    assert bd["wrong_room"] == 0.0
    assert bd["checkpoint_success"] == pytest.approx(RAILS_CHECKPOINT_REWARD)


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
    """cp05: return from 203 is room_enter_from only (no in-control dwell)."""
    first_entry = _planner(start_index=3)
    hall = ProgressTracker()
    hall.observe_cutscene("106:1:s0")
    assert first_entry.advance_if_success(_state("106"), progress=hall)

    return_entry = _planner(start_index=5)
    ret = ProgressTracker()
    assert return_entry.advance_if_success(
        _state("106"),
        progress=ret,
        prev_state=_state("203"),
    )

    wrong_return = _planner(start_index=5)
    bad = ProgressTracker()
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
    progress.note_leg_kills("11A", 1)
    assert planner.advance_if_success(placed, progress=progress)


def test_cp39_leg_east_stairs_101_room_enter() -> None:
    """cp39 reset (index 39) -> leg 40 is east_stairs_101; entering 10B completes."""
    planner = _planner(start_index=_idx("back_passage_post_crest_10A") + 1)
    assert planner.current_objective()["checkpoint_id"] == "east_stairs_101"
    progress = ProgressTracker()
    assert not planner.advance_if_success(_state("10A"), progress=progress)
    assert not planner.advance_if_success(_state("101"), progress=progress)
    assert planner.advance_if_success(_state("10B"), progress=progress)


def test_cp01_completes_on_tea_room_enter() -> None:
    planner = _planner(start_index=_idx("kenneth_104"))
    progress = ProgressTracker()
    assert not planner.advance_if_success(_state("105"), progress=progress)
    assert planner.advance_if_success(_state("104"), progress=progress)


def test_cp01_capture_does_not_require_kenneth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    bridge = MagicMock()
    bridge.save_savestate.side_effect = (
        lambda path: Path(path).write_bytes(b"state")
    )
    planner = _planner(start_index=_idx("kenneth_104") + 1)
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
        _progress=ProgressTracker(),
        _step_count=300,
        _read_state=lambda track_items=False: _state("104", inventory=["emblem"]),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )
    proposal = capture_successor_cell(
        env,
        _state("104", inventory=["emblem"]),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert proposal is not None
    assert proposal["checkpoint_id"] == "kenneth_104"
    assert proposal["checkpoint_index"] == 1


def _barry_slots(*, beretta_qty: int = 15, spray: bool = True) -> list[tuple[str, int]]:
    slots: list[tuple[str, int]] = [("knife", 1), ("beretta", int(beretta_qty))]
    if spray:
        slots.append(("first_aid_spray_alt", 1))
    slots.append(("emblem", 1))
    return slots


def _barry_state(room: str, *, beretta_qty: int = 15, spray: bool = True) -> dict:
    slots = _barry_slots(beretta_qty=beretta_qty, spray=spray)
    return _state(room, inventory=[n for n, _ in slots], inventory_slots=slots)


def test_barry_return_needs_kenneth_then_104_to_105() -> None:
    """104→105 before Kenneth does not complete cp02; after the flag it does."""
    planner = _planner(start_index=_idx("barry_return_105"))
    progress = ProgressTracker()
    _settle(progress, "105", steps=90)
    assert not planner.advance_if_success(
        _barry_state("105"),
        progress=progress,
        prev_state=_barry_state("105"),
    )
    progress.note_leg_room_transition("104", "105")
    assert not planner.advance_if_success(
        _barry_state("105"),
        progress=progress,
        prev_state=_barry_state("104"),
    )
    progress.observe_cutscene("104:0:s0")
    assert planner.advance_if_success(
        _barry_state("105"),
        progress=progress,
        prev_state=_barry_state("104"),
    )


def test_barry_return_door_mint_is_not_kenneth() -> None:
    """Door key ``104:0`` / dining mint are not Kenneth; ``104:0:s0`` is."""
    planner = _planner(start_index=_idx("barry_return_105"))
    progress = ProgressTracker()
    progress.note_leg_room_transition("104", "105")
    progress.observe_cutscene("104:0")
    progress.observe_cutscene("105:barry_return")
    assert not planner.advance_if_success(
        _barry_state("105"),
        progress=progress,
        prev_state=_barry_state("104"),
    )
    progress.observe_cutscene("104:0:s0")
    assert planner.advance_if_success(
        _barry_state("105"),
        progress=progress,
        prev_state=_barry_state("104"),
    )


def test_barry_return_ignores_spray_and_ammo() -> None:
    planner = _planner(start_index=_idx("barry_return_105"))
    progress = ProgressTracker()
    progress.note_leg_room_transition("104", "105")
    progress.observe_cutscene("104:4:s0")
    assert planner.advance_if_success(
        _barry_state("105", spray=False, beretta_qty=14),
        progress=progress,
        prev_state=_barry_state("104", spray=False, beretta_qty=14),
    )


def test_upper_hall_203_is_room_enter_settle_without_cutscene() -> None:
    """First climb to 203 has no cinema; cutscene gate would softlock cp04 forever."""
    planner = _planner(start_index=_idx("upper_hall_203"))
    assert planner.advance_if_success(_state("203"), progress=ProgressTracker())


def test_main_hall_completes_on_room_enter_without_cutscene_key() -> None:
    planner = _planner(start_index=_idx("main_hall_106"))
    spoof = ProgressTracker()
    _settle(spoof, "106", steps=2)
    assert planner.advance_if_success(_state("106"), progress=spoof)


def test_l_passage_enter_then_ammo_are_separate_legs() -> None:
    enter = _planner(start_index=_idx("l_passage_enter_108"))
    assert enter.current_objective()["checkpoint_id"] == "l_passage_enter_108"
    # Entering 108 alone completes the doorway cell (no ammo required).
    assert enter.advance_if_success(_state("108"), progress=ProgressTracker())
    assert enter.current_objective()["checkpoint_id"] == "ammo_108"

    ammo = _planner(start_index=_idx("ammo_108"))
    progress = ProgressTracker()
    # Already in the L Passage: pickup + both hallway kills, no door re-check.
    assert not ammo.advance_if_success(_state("108"), progress=progress)
    progress.note_leg_acquired("handgun_bullets")
    assert not ammo.advance_if_success(
        _state("108", inventory=["handgun_bullets"]),
        progress=progress,
    )
    progress.note_leg_kills("108", 2)
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

    assert planner.current_objective()["checkpoint_id"] == "star_crest_117"
    assert not planner.advance_if_success(
        _state("117", gallery_progress=GALLERY_STEP_VALUES[5]),
        progress=progress,
    )
    # Reveal alone is not enough — crest pickup requires taking the star crest.
    assert not planner.advance_if_success(
        _state("117", gallery_progress=0, gallery_puzzle_solved=True),
        progress=progress,
    )
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


def test_shotgun_rescue_requires_reentry_and_shotgun_not_cutscene() -> None:
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
    assert no_cutscene.advance_if_success(
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


def test_fresh_emblem_has_in_room_wayfinder() -> None:
    """Fresh start is already in 105; compass must point at the wooden emblem."""
    encoder = ObsEncoder(ROOMS, _graph(), curriculum_stage_index=1)
    planner = _planner(start_index=0)
    assert planner.current_objective()["checkpoint_id"] == "emblem_105"
    assert planner.objective_type() == "pickup"
    assert planner.next_waypoint_room() == "105"
    # Dining spawn is west of the fireplace emblem (32700, 8300).
    state = _state("105", inventory=["knife", "beretta"], x=18000, z=4000)
    goal = encoder.encode_goal(state, planner)
    assert goal[GOAL_IDX["in_target_room"]] == 1.0
    assert goal[GOAL_IDX["obj_pickup"]] == 1.0
    assert goal[GOAL_IDX["doors_available"]] == 1.0
    assert goal[GOAL_IDX["door_distance"]] > 0.2
    assert goal[GOAL_IDX["door_delta_x"]] > 0.0


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
    # Lookahead from east stairs 1F after box prep: climb 207 … ammo_20D, attic, yawn.
    planner = _planner(start_index=_idx("east_stairs_201_to_yawn"))
    goal = encoder.encode_goal(
        _state("10B", inventory=["shield_key", "shotgun"]),
        planner,
    )
    slots = goal[GOAL_BASE_DIM:].reshape(
        GOAL_LOOKAHEAD_SLOTS, GOAL_LOOKAHEAD_SLOT_DIM
    )
    assert slots[0, 0] == 1.0
    assert slots[0, 1] == encoder._room_idx_norm("207")
    pickup_slots = [i for i in range(GOAL_LOOKAHEAD_SLOTS) if slots[i, 3 + 1] == 1.0]
    assert pickup_slots, "expected a pickup in lookahead"
    assert any(
        planner.peek_objective(offset) is not None
        and planner.peek_objective(offset)["checkpoint_id"] == "yawn_moon_210"
        for offset in range(0, _ROUTE_N - _idx("east_stairs_201_to_yawn"))
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
        _checkpoint_captured=False,
    )
    progress.claim_checkpoint_success()
    assert RE1Env._termination_flags(env, _state("105")) == (False, False, None)
    progress.claim_checkpoint_success()
    assert RE1Env._termination_flags(env, _state("104")) == (False, False, None)
    env._checkpoint_captured = True
    assert RE1Env._termination_flags(env, _state("104")) == (
        True,
        False,
        "checkpoint_success",
    )


def test_yawn_box_prep_ignores_lab_timer_requires_keys_and_firepower() -> None:
    from re1_rl.yawn_box_prep_checkpoint import WIND_CREST_ITEM_ID

    planner = _planner(start_index=_idx("yawn_box_prep_118"))
    assert planner.current_objective()["checkpoint_id"] == "yawn_box_prep_118"
    assert "118" in BOX_ROOMS

    box = [(0, 0)] * 48
    box[0] = (WIND_CREST_ITEM_ID, 1)
    prev = _state("118")
    ready_inv = [
        "beretta",
        "handgun_bullets",
        "shield_key",
        "shotgun",
        "acid_rounds",
        "armor_key",
        "shotgun_shells",
        "bazooka_acid",
    ]

    missing_key = _state("10B")
    missing_key["lab_timer"] = 0
    missing_key["inventory"] = [n for n in ready_inv if n != "armor_key"]
    missing_key["box_cache"] = box
    assert not planner.advance_if_success(
        missing_key, progress=ProgressTracker(), prev_state=prev
    )

    ticking = _state("10B")
    ticking["lab_timer"] = 8600
    ticking["inventory"] = list(ready_inv)
    ticking["box_cache"] = box
    assert planner.advance_if_success(
        ticking, progress=ProgressTracker(), prev_state=prev
    )


def test_cp89_start_is_climb_to_207_not_reenter_10b() -> None:
    """Box-prep cell sits in 10B; next real door is 207, not another enter-10B."""
    planner = _planner(start_index=_idx("east_stairs_201_to_yawn"))
    assert planner.current_objective()["checkpoint_id"] == "east_stairs_201_to_yawn"
    assert not planner.advance_if_success(
        _state("10B"), progress=ProgressTracker()
    )
    assert planner.skip_spawn_satisfied_room_enters("10B") == 0


def test_spawn_in_exit_room_skips_tautological_room_enter() -> None:
    """Richard cp84 sits in 204; next CP is room_enter 204 — skip or 1-step reset."""
    planner = _planner(start_index=_idx("richard_forced_return_204"))
    assert planner.current_objective()["checkpoint_id"] == "richard_forced_return_204"
    already_there = _planner(start_index=_idx("richard_forced_return_204"))
    assert already_there.advance_if_success(
        _state("204"), progress=ProgressTracker()
    )

    skipped = planner.skip_spawn_satisfied_room_enters("204")
    assert skipped == 1
    assert planner.current_objective()["checkpoint_id"] == "east_stairs_201_post_richard"
    assert planner.skip_spawn_satisfied_room_enters("204") == 0


def test_richard_checkpoint_accepts_forced_settle_in_204() -> None:
    planner = _planner(start_index=_idx("richard_cutscene_20D"))
    progress = ProgressTracker()
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
    assert a["reset_source"] in {"route_cell", "route_initial"}
    assert a["reset_source"] not in {"pb", "archive"}
    if a["reset_source"] == "route_initial":
        assert a["route_start_index"] == 0
        assert "pb_bundle" not in a
    else:
        assert a["route_start_index"] == 19
        assert "pb_bundle" in a


def test_default_mix_equal_fresh_and_each_loadable_cell(tmp_path: Path) -> None:
    """Fresh start is its own slot, equal with each cp00–cp95 cell — not cp00."""
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in (0, 18, 95)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 98)),
    }
    counts: dict[str, int] = {"fresh": 0, "cp00": 0, "cp18": 0, "cp95": 0}
    for seed in range(8000):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        if opts["reset_source"] == "route_initial":
            counts["fresh"] += 1
            assert opts["route_start_index"] == 0
            assert "pb_bundle" not in opts
            assert opts["leg_span"] == 1
        else:
            start = int(opts["route_start_index"])
            cell = start - 1
            counts[f"cp{cell:02d}"] += 1
            assert opts["leg_span"] == 1
    total = sum(counts.values())
    assert total == 8000
    for key, n in counts.items():
        assert n / total == pytest.approx(0.25, abs=0.03), f"{key}={n}"


def test_cp95_playthrough_is_the_yawn_fight_only(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, 95)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 98)),
    }
    monkey_hits = 0
    for seed in range(200):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        if opts.get("route_start_index") == 96:
            monkey_hits += 1
            assert opts["leg_span"] == 1
            assert opts["reset_source"] == "route_cell"
    assert monkey_hits > 0


def test_terminal_cp96_is_never_loadable(tmp_path: Path) -> None:
    """cp96 has no next hunt target — no agent may reset into it."""
    import shutil

    route_src = ROOT / "data" / "yawn_checkpoint_route.json"
    shutil.copy(route_src, tmp_path / "yawn_checkpoint_route.json")
    cells = []
    for idx in (95, 96):
        row = _write_cell(tmp_path, idx)
        row["next_checkpoint_id"] = "" if idx == 96 else "yawn_moon_210"
        cells.append(row)
    manifest = {"schema_version": 1, "route_id": "test", "cells": cells}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_path": "yawn_checkpoint_route.json",
        "route_steps": list(range(1, _ROUTE_N + 1)),
    }
    loadable = iter_loadable_cells(tmp_path, stage)
    indices = {int(r["checkpoint_index"]) for r in loadable}
    assert 96 not in indices
    assert 95 in indices


def test_empty_next_checkpoint_id_excluded_even_without_route_path(
    tmp_path: Path,
) -> None:
    cells = [_write_cell(tmp_path, 18), _write_cell(tmp_path, 96)]
    cells[1]["next_checkpoint_id"] = ""
    manifest = {"schema_version": 1, "route_id": "test", "cells": cells}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {"route_id": "test", "cells_manifest": "manifest.json"}
    loadable = iter_loadable_cells(tmp_path, stage)
    indices = {int(r["checkpoint_index"]) for r in loadable}
    assert 18 in indices
    assert 96 not in indices


def test_reset_mix_uniform_over_eligible_cells_and_fresh(
    tmp_path: Path,
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
    per_start: dict[int, int] = {}
    for seed in range(4000):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        start = int(opts["route_start_index"])
        per_start[start] = per_start.get(start, 0) + 1
    total = sum(per_start.values())
    # fresh + 3 cells → 4 equal slots
    assert set(per_start) == {0, 19, 20, 21}
    for start in (0, 19, 20, 21):
        assert per_start[start] / total == pytest.approx(0.25, abs=0.04)


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


def test_reset_frontier_fight_only_env(
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
    monkeypatch.setenv("RE1_YAWN_RESET_FRONTIER_FIGHT_ONLY", "1")
    for seed in range(20):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["route_start_index"] == 19
        assert opts["reset_source"] == "route_cell_frontier_fight"
        assert str(opts["pb_bundle"]["state_path"]).replace("\\", "/").endswith(
            "states/cp18/cell.State"
        )


def test_reset_pin_index_env_forces_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 34)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 40)),
    }
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_INDEX", "33")
    for seed in range(20):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["route_start_index"] == 34
        assert opts["reset_source"] == "route_cell_pin"
        assert str(opts["pb_bundle"]["state_path"]).replace("\\", "/").endswith(
            "states/cp33/cell.State"
        )


def test_reset_pin_file_hot_reload_overrides_launcher_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 34)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 40)),
    }
    pin_file = tmp_path / "yawn_reset_pin.env"
    pin_file.write_text("RE1_YAWN_RESET_PIN_INDEX=33\n", encoding="utf-8")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_FILE", str(pin_file))
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_INDEX", "18")
    opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(0))
    assert opts["route_start_index"] == 34
    pin_file.write_text("RE1_YAWN_RESET_PIN_INDEX=19\n", encoding="utf-8")
    opts2 = sample_one_leg_options(tmp_path, stage, rng=random.Random(0))
    assert opts2["route_start_index"] == 20


def test_reset_pin_file_resolved_from_project_root_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 34)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 40)),
    }
    pin_dir = tmp_path / "data"
    pin_dir.mkdir()
    (pin_dir / "yawn_reset_pin.env").write_text(
        "RE1_YAWN_RESET_PIN_INDEX=33\n", encoding="utf-8"
    )
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_FILE", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_INDEX", "18")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_FILE", " ")
    opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(0))
    assert opts["route_start_index"] == 34
    assert opts["reset_source"] == "route_cell_pin"


def test_reset_pin_range_env_samples_inclusive_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 38)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 45)),
    }
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INCLUDE_FRESH", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_RANGE", "27-37")
    starts: set[int] = set()
    for seed in range(80):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["reset_source"] == "route_cell_pin_range"
        start = int(opts["route_start_index"])
        assert 28 <= start <= 38
        starts.add(start)
    assert starts >= {28, 38}


def test_reset_pin_range_include_fresh_mixes_init_and_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in (0, 3, 8, 18)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 45)),
    }
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_WEIGHTS", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_RANGE", "0-8")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_INCLUDE_FRESH", "1")
    counts: dict[str, int] = {"fresh": 0, "cp00": 0, "cp03": 0, "cp08": 0, "other": 0}
    for seed in range(4000):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        if opts["reset_source"] == "route_initial":
            counts["fresh"] += 1
            assert opts["route_start_index"] == 0
            assert "pb_bundle" not in opts
        else:
            cell = int(opts["route_start_index"]) - 1
            key = f"cp{cell:02d}"
            counts[key if key in counts else "other"] += 1
    assert counts["other"] == 0
    total = sum(counts.values())
    for key in ("fresh", "cp00", "cp03", "cp08"):
        assert counts[key] / total == pytest.approx(0.25, abs=0.04), f"{key}={counts[key]}"


def test_reset_pin_range_include_fresh_empty_range_is_fresh_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in (18, 40)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 45)),
    }
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_WEIGHTS", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_RANGE", "0-8")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_INCLUDE_FRESH", "1")
    for seed in range(40):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["reset_source"] == "route_initial"
        assert opts["route_start_index"] == 0
        assert "pb_bundle" not in opts


def test_reset_pin_set_include_fresh_is_exclusive_mix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in (0, 2, 6, 18, 40)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 45)),
    }
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_RANGE", raising=False)
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_WEIGHTS", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_SET", "0,2,6")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_SET_WEIGHT", "1")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_INCLUDE_FRESH", "1")
    counts: dict[str, int] = {"fresh": 0, "cp00": 0, "cp02": 0, "cp06": 0, "other": 0}
    for seed in range(4000):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        if opts["reset_source"] == "route_initial":
            counts["fresh"] += 1
            assert opts["route_start_index"] == 0
            assert "pb_bundle" not in opts
        else:
            cell = int(opts["route_start_index"]) - 1
            key = f"cp{cell:02d}"
            counts[key if key in counts else "other"] += 1
    assert counts["other"] == 0
    total = sum(counts.values())
    for key in ("fresh", "cp00", "cp02", "cp06"):
        assert counts[key] / total == pytest.approx(0.25, abs=0.04), f"{key}={counts[key]}"


def test_reset_pin_range_latest_mix_samples_floor_and_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import Counter

    pin_file = tmp_path / "pin.env"
    pin_file.write_text(
        "RE1_YAWN_RESET_PIN_RANGE=54-100\nRE1_YAWN_RESET_PIN_WEIGHTS=latest:50\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_FILE", str(pin_file))
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 56)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 60)),
    }
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_RANGE", "54-100")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_WEIGHTS", "latest:50")
    counts: Counter[int] = Counter()
    for seed in range(2000):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["reset_source"] == "route_cell_pin_range_latest"
        start = int(opts["route_start_index"])
        assert 55 <= start <= 56
        counts[start] += 1
    assert counts[56] > 700
    assert counts[55] > 200


def test_reset_pin_range_latest_mix_tracks_new_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pin_file = tmp_path / "pin.env"
    pin_file.write_text(
        "RE1_YAWN_RESET_PIN_RANGE=54-100\nRE1_YAWN_RESET_PIN_WEIGHTS=latest:100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_FILE", str(pin_file))
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(54, 56)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 60)),
    }
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_RANGE", "54-100")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_WEIGHTS", "latest:50")
    for seed in range(20):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["route_start_index"] == 56

    manifest["cells"].append(_write_cell(tmp_path, 57))
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(0))
    assert opts["route_start_index"] == 58


def test_parse_pin_weights_accepts_percentages_and_fractions() -> None:
    from re1_rl.yawn_rails import PIN_WEIGHT_LATEST_KEY, parse_pin_weights

    assert parse_pin_weights("33:20,36:30,40:50") == {
        33: pytest.approx(0.2),
        36: pytest.approx(0.3),
        40: pytest.approx(0.5),
    }
    assert parse_pin_weights("33=0.25,36=0.75") == {
        33: pytest.approx(0.25),
        36: pytest.approx(0.75),
    }
    assert parse_pin_weights("33:10,36:10") == {33: 0.5, 36: 0.5}
    assert parse_pin_weights("latest:60,33:40") == {
        PIN_WEIGHT_LATEST_KEY: pytest.approx(0.6),
        33: pytest.approx(0.4),
    }


def test_reset_pin_weights_latest_tracks_newest_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 40)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 45)),
    }
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_WEIGHTS", "latest:100")
    for seed in range(20):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["route_start_index"] == 40

    manifest["cells"].append(_write_cell(tmp_path, 42))
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(0))
    assert opts["route_start_index"] == 43


def test_reset_pin_weights_latest_merges_with_explicit_same_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 40)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 45)),
    }
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_WEIGHTS", "latest:50,39:50")
    for seed in range(20):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["route_start_index"] == 40


def test_reset_pin_weights_env_samples_by_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections import Counter

    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 45)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 50)),
    }
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_RANGE", raising=False)
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_SET", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_WEIGHTS", "33:90,36:10")
    counts: Counter[int] = Counter()
    for seed in range(1000):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["reset_source"] == "route_cell_pin_weights"
        counts[int(opts["route_start_index"])] += 1
    assert counts[34] > 800
    assert counts[37] > 50
    assert counts[34] + counts[37] == 1000


def test_reset_pin_weights_renormalizes_when_some_cells_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 40)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 45)),
    }
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_WEIGHTS", "33:50,99:50")
    for seed in range(20):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["route_start_index"] == 34


def test_reset_pin_weights_overrides_pin_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_file = tmp_path / "pin.env"
    pin_file.write_text(
        "RE1_YAWN_RESET_PIN_RANGE=27-37\nRE1_YAWN_RESET_PIN_WEIGHTS=40:100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_FILE", str(pin_file))
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 45)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 50)),
    }
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_RANGE", "27-37")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_WEIGHTS", "40:100")
    for seed in range(20):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["reset_source"] == "route_cell_pin_weights"
        assert opts["route_start_index"] == 41


def test_reset_pin_set_env_blends_with_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "schema_version": 1,
        "route_id": "test",
        "cells": [_write_cell(tmp_path, i) for i in range(18, 55)],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage = {
        "route_id": "test",
        "cells_manifest": "manifest.json",
        "route_steps": list(range(1, 60)),
    }
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_INDEX", raising=False)
    monkeypatch.delenv("RE1_YAWN_RESET_PIN_RANGE", raising=False)
    monkeypatch.delenv("RE1_YAWN_PAYFORWARD_RIPPLE", raising=False)
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_SET", "37,40,44")
    monkeypatch.setenv("RE1_YAWN_RESET_PIN_SET_WEIGHT", "1.0")
    starts: set[int] = set()
    for seed in range(40):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        assert opts["reset_source"] == "route_cell_pin_set"
        starts.add(int(opts["route_start_index"]))
    assert starts <= {38, 41, 45}
    assert starts >= {38, 45}

    monkeypatch.setenv("RE1_YAWN_RESET_PIN_SET_WEIGHT", "0.0")
    other = sample_one_leg_options(tmp_path, stage, rng=random.Random(0))
    assert other["reset_source"] != "route_cell_pin_set"


def test_playthrough_curriculum_spans_remaining_route(tmp_path: Path) -> None:
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
        "episode_mode": "play_through",
    }
    cell_opts = None
    for seed in range(40):
        opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(seed))
        if opts["reset_source"] == "route_cell":
            cell_opts = opts
            break
    assert cell_opts is not None
    assert cell_opts["route_start_index"] == 19
    assert cell_opts["leg_span"] == 10  # remaining through end, not the old 6-cap
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
    assert one_leg["legs_per_episode"] == 1
    assert one_leg["route_id"] == "yawn_quest_v2"
    assert one_leg["route_steps"][-1] == _ROUTE_N


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
    assert chosen["reset_source"] in {"route_cell", "route_initial"}


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


def test_terminal_yawn_moon_capture_proposes_cp96(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Last route leg (yawn_moon_210) must still install cp96."""
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "1")
    bridge = MagicMock()
    bridge.save_savestate.side_effect = (
        lambda path: Path(path).write_bytes(b"state")
    )
    moon_idx = _idx("yawn_moon_210")
    planner = _planner(start_index=moon_idx)
    planner._index = moon_idx + 1
    inv = [
        "shield_key",
        "shotgun",
        "shotgun_shells",
        "moon_crest",
        "beretta",
    ]
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
        _read_state=lambda track_items=False: _state("210", inventory=inv),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )

    proposal = capture_successor_cell(
        env,
        _state("210", inventory=inv),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert proposal is not None
    assert proposal["checkpoint_index"] == moon_idx
    assert proposal["checkpoint_id"] == "yawn_moon_210"
    assert proposal["next_checkpoint_id"] == ""
    assert bridge.save_savestate.called


def test_orphan_disk_meta_is_not_incumbent(tmp_path: Path) -> None:
    from re1_rl.yawn_rails_sync import (
        CELL_META_NAME,
        _existing_cell_quality,
        yawn_rails_root,
    )

    yr = yawn_rails_root(tmp_path)
    slot = yr / "cells" / "cp00"
    slot.mkdir(parents=True)
    (slot / CELL_META_NAME).write_text(
        json.dumps({"quality": [96, 45, 100, 4, 1, 0, -30, -99999999]}),
        encoding="utf-8",
    )
    assert _existing_cell_quality(yr, 0) is None
    (yr / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cells": [
                    {
                        "checkpoint_index": 0,
                        "quality": [96, 45, 100, 4, 1, 0, -30, -12],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    q = _existing_cell_quality(yr, 0)
    assert q is not None
    assert q[7] == -12


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


def test_local_cas_quality_beats_installs_regardless_of_sidecar_cutscenes(
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
    assert try_install_yawn_cell(
        tmp_path,
        checkpoint_index=2,
        staged_dir=healthier,
        quality=[96, 60, 0, 4, 1, 0],
        row={"checkpoint_id": "barry_return_105", "room_id": "105"},
    )
    assert (good / CELL_STATE_NAME).read_bytes() == b"THIN"


def test_capture_settles_cutscene_before_savestate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """star_crest / gallery pickup succeeds mid-cinema; do not drop the cell."""
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "1")
    bridge = MagicMock()
    bridge.save_savestate.side_effect = (
        lambda path: Path(path).write_bytes(b"state")
    )
    # After advancing gallery_portrait_6 → star_crest is active; success
    # on star_crest leaves waypoint on the next leg (completed = that idx).
    planner = _planner(start_index=_idx("star_crest_117") + 1)
    # First read is post-settle; later reads are pre/post savestate probes.
    reads = [
        _state("117", in_control=True),
        _state("117", in_control=True),
        _state("117", in_control=True),
    ]

    def _read_state(track_items=False):
        if reads:
            return reads.pop(0)
        return _state("117", in_control=True)

    skip_calls = {"n": 0}

    def _skip():
        skip_calls["n"] += 1
        return 120, False

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
        _step_count=900,
        _skip_uncontrolled=_skip,
        _read_state=_read_state,
        _auto_accept_pause_pickup_modal=lambda: False,
        _try_dismiss_orphan_item_menu=lambda: (True, {"cleared": True, "skipped": True}),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )
    proposal = capture_successor_cell(
        env,
        _state("117", in_control=False),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert skip_calls["n"] == 1
    assert proposal is not None
    assert proposal["checkpoint_id"] == "star_crest_117"
    assert bridge.save_savestate.called


def test_capture_settles_item_menu_before_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Post-pickup ITEM pause is not turbo-skippable — dismiss then capture."""
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "1")
    bridge = MagicMock()
    bridge.save_savestate.side_effect = (
        lambda path: Path(path).write_bytes(b"state")
    )
    planner = _planner(start_index=_idx("shotgun_116") + 1)
    reads = [
        _state("116", in_control=True, inventory=["shotgun", "beretta"]),
        _state("116", in_control=True, inventory=["shotgun", "beretta"]),
        _state("116", in_control=True, inventory=["shotgun", "beretta"]),
    ]
    calls = {"accept": 0, "dismiss": 0, "skip": 0}

    def _read_state(track_items=False):
        if reads:
            return reads.pop(0)
        return _state("116", in_control=True, inventory=["shotgun", "beretta"])

    def _accept():
        calls["accept"] += 1
        return True

    def _dismiss():
        calls["dismiss"] += 1
        return True, {"cleared": True, "path": "triangle_cancel", "frames": 40}

    def _skip():
        calls["skip"] += 1
        return 0, False

    # Seed an inferior curated cell so quality log shows WOULD_INSTALL / compare.
    cell = tmp_path / "states/yawn_rails/cells/cp27"
    cell.mkdir(parents=True)
    (cell / "meta.json").write_text(
        json.dumps(
            {
                "checkpoint_index": _idx("shotgun_116"),
                "checkpoint_id": "shotgun_116",
                "quality": [20, 10, 0, 5, 1, 0, 0],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "states/yawn_rails/manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "route_id": "test",
                "cells": [
                    {
                        "checkpoint_index": _idx("shotgun_116"),
                        "checkpoint_id": "shotgun_116",
                        "quality": [20, 10, 0, 5, 1, 0, 0],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

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
        _step_count=500,
        _auto_accept_pause_pickup_modal=_accept,
        _try_dismiss_orphan_item_menu=_dismiss,
        _skip_uncontrolled=_skip,
        _read_state=_read_state,
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )
    proposal = capture_successor_cell(
        env,
        _state("116", in_control=False, inventory=["shotgun", "beretta"], hp=96),
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
    )
    assert calls == {"accept": 1, "dismiss": 1, "skip": 1}
    assert proposal is not None
    assert proposal["checkpoint_id"] == "shotgun_116"
    out = capsys.readouterr().out
    assert "[yawn_capture] quality" in out
    assert "payforward=" in out
    assert "shotgun_116" in out


def test_barry_return_capture_ignores_kenneth(
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
    progress.observed_cutscenes.add("105:2:s1")  # generic dining cam, not Kenneth
    progress.observed_cutscenes.add("105:barry_return")  # fake door mint is not Kenneth
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
        _read_state=lambda track_items=False: _state(
            "105", inventory=["knife", "beretta", "emblem"]
        ),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {
            "schema_version": 1,
            "progress": {"observed_cutscenes": sorted(progress.observed_cutscenes)},
            "episode_history": {"room_entries": [["105", 100]]},
        },
    )
    barry_state = _state("105", inventory=["knife", "beretta", "emblem"])
    proposal = capture_successor_cell(
        env, barry_state, {"checkpoint_success": RAILS_CHECKPOINT_REWARD}
    )
    assert proposal is not None
    assert proposal["checkpoint_id"] == "barry_return_105"


def test_east_stairs_post_storeroom_capture_requires_two_free_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cp42 capture refuses when on-person inventory has fewer than 2 empty slots."""
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    bridge = MagicMock()
    bridge.save_savestate.side_effect = (
        lambda path: Path(path).write_bytes(b"state")
    )
    planner = _planner(
        start_index=_idx("east_stairs_101_post_storeroom") + 1
    )
    six_items = (
        ("knife", 0),
        ("beretta", 14),
        ("handgun_bullets", 16),
        ("shield_key", 1),
        ("green_herb", 1),
        ("chemical", 1),
    )
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
        _progress=None,
        _step_count=300,
        _read_state=lambda track_items=False: _state("10B"),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )
    monkeypatch.setattr(
        "re1_rl.go_explore_capture.compute_quality",
        lambda *_args, **_kwargs: [96, 85, 68, 14, 1, 0, 0],
    )

    crowded = _state(
        "10B",
        inventory_slots=[*six_items, ("shotgun", 1), (0, 0)],
    )
    assert (
        capture_successor_cell(
            env, crowded, {"checkpoint_success": RAILS_CHECKPOINT_REWARD}
        )
        is None
    )
    bridge.save_savestate.assert_not_called()

    ok_state = _state(
        "10B",
        inventory_slots=[*six_items, (0, 0), (0, 0)],
    )
    proposal = capture_successor_cell(
        env, ok_state, {"checkpoint_success": RAILS_CHECKPOINT_REWARD}
    )
    assert proposal is not None
    assert proposal["checkpoint_id"] == "east_stairs_101_post_storeroom"


def test_west_stairs_return_capture_requires_two_free_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cp57 capture refuses when leaving save room 100 with fewer than 2 empty slots."""
    monkeypatch.setenv("RE1_YAWN_RAILS_SYNC", "0")
    bridge = MagicMock()
    bridge.save_savestate.side_effect = (
        lambda path: Path(path).write_bytes(b"state")
    )
    planner = _planner(start_index=_idx("west_stairs_return_10B") + 1)
    six_items = (
        ("knife", 0),
        ("beretta", 14),
        ("handgun_bullets", 16),
        ("shield_key", 1),
        ("green_herb", 1),
        ("chemical", 1),
    )
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
        _progress=None,
        _step_count=300,
        _read_state=lambda track_items=False: _state("101"),
    )
    monkeypatch.setattr(
        "re1_rl.yawn_rails.dump_episode_sidecar",
        lambda *_args, **_kwargs: {"schema_version": 1},
    )
    monkeypatch.setattr(
        "re1_rl.go_explore_capture.compute_quality",
        lambda *_args, **_kwargs: [96, 85, 68, 14, 1, 0, 0],
    )

    crowded = _state(
        "101",
        inventory_slots=[*six_items, ("shotgun", 1), (0, 0)],
    )
    assert (
        capture_successor_cell(
            env, crowded, {"checkpoint_success": RAILS_CHECKPOINT_REWARD}
        )
        is None
    )
    assert yawn_capture_ineligible_reason(env) == "inventory_free_slots"
    bridge.save_savestate.assert_not_called()

    ok_state = _state(
        "101",
        inventory_slots=[*six_items, (0, 0), (0, 0)],
    )
    proposal = capture_successor_cell(
        env, ok_state, {"checkpoint_success": RAILS_CHECKPOINT_REWARD}
    )
    assert proposal is not None
    assert yawn_capture_ineligible_reason(env) is None
    assert proposal["checkpoint_id"] == "west_stairs_return_10B"


def test_capture_ineligible_claws_back_checkpoint_reward() -> None:
    """Hard capture ineligibility replaces +8 checkpoint_success with -4 failure."""
    progress = ProgressTracker(leg_span=1)
    progress.checkpoint_success = True
    env = SimpleNamespace(
        _progress=progress,
        _stage={"mode": "yawn_rails"},
    )
    setattr(env, "_yawn_capture_ineligible_reason", "inventory_free_slots")
    breakdown = {
        "checkpoint_success": RAILS_CHECKPOINT_REWARD,
        "new_room": 4.0,
    }
    RE1Env._apply_yawn_capture_ineligibility_penalty(env, breakdown)
    assert breakdown["checkpoint_success"] == 0.0
    assert breakdown["checkpoint_capture_ineligible"] == RAILS_CAPTURE_INELIGIBLE_PENALTY
    assert breakdown["new_room"] == 0.0
    assert progress.capture_ineligible_breached
    assert not progress.checkpoint_success


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
