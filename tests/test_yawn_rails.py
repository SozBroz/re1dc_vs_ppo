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
from re1_rl.reward import RAILS_CHECKPOINT_REWARD, compute_reward
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


def _graph() -> RoomGraph:
    return RoomGraph(
        DOORS,
        DOORS_RDT,
        valid_rooms=load_valid_rooms(ROOMS),
    )


def _planner(start_index: int = 0) -> WaypointPlanner:
    return WaypointPlanner(
        ROUTE,
        route_steps=list(range(1, 54)),
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
    # Cells may be wiped between capture cycles; empty manifest is valid.
    assert validate_manifest_cells(ROOT, curriculum, require_contiguous_prefix=0) == []
    assert curriculum["max_steps"] == 2700  # 6 min at 8 frames/step and 60fps.
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
    assert [(cp["room_id"], cp["checkpoint_id"]) for cp in route[43:]] == [
        ("20D", "richard_cutscene_20D"),
        ("204", "richard_forced_return_204"),
        ("201", "east_stairs_201_post_richard"),
        ("101", "east_stairs_101_post_richard"),
        ("11B", "yawn_box_prep_11B"),
        ("101", "east_stairs_101_to_yawn"),
        ("201", "east_stairs_201_to_yawn"),
        ("20D", "ammo_20D"),
        ("20E", "attic_entry_20E"),
        ("210", "yawn_moon_210"),
    ]
    for checkpoint in route:
        condition_text = json.dumps(checkpoint["success_condition"])
        for item in checkpoint["items_gained"]:
            assert f'"item": "{item}"' in condition_text


def test_zero_coordinate_rdt_rows_are_not_walkable_edges() -> None:
    graph = _graph()
    assert graph.get_exit("20E", "100") is None
    assert graph.get_exit("20E", "210") is not None


def test_rails_nav_crumbs_keep_full_exploration_magnitudes() -> None:
    planner = _planner()
    progress = ProgressTracker()
    progress.seed_spawn_room("105")
    _, bd = compute_reward(
        _state("105"),
        _state("104", inventory=["emblem"]),
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["new_room"] == pytest.approx(4.0)
    assert bd["checkpoint_success"] == 0.0


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


def test_lockpick_is_required_on_return_to_106_not_first_entry() -> None:
    first_entry = _planner(start_index=3)
    assert first_entry.advance_if_success(_state("106"), progress=ProgressTracker())

    return_entry = _planner(start_index=5)
    prev = _state("203")
    state = _state("106", inventory=["lockpick"])
    # Key item already in inventory satisfies acquired_item (no leg_acquired needed).
    assert return_entry.advance_if_success(
        state,
        progress=ProgressTracker(),
        prev_state=prev,
    )

    acquired = ProgressTracker()
    acquired.note_leg_acquired("lockpick")
    wrong_return = _planner(start_index=5)
    assert not wrong_return.advance_if_success(
        state,
        progress=acquired,
        prev_state=_state("201"),
    )


def test_lockpick_already_held_passes_without_leg_acquire() -> None:
    planner = _planner(start_index=5)
    assert planner.advance_if_success(
        _state("106", inventory=["lockpick"]),
        progress=ProgressTracker(),
        prev_state=_state("203"),
    )


def test_lockpick_snap_skips_203_and_finishes_barry_return() -> None:
    """Getting lockpick early snaps forward past barry_hall_return (cp05)."""
    planner = _planner(start_index=3)  # main_hall_106 — before 203
    assert planner.index_of_item_gain_checkpoint("lockpick") == 5
    assert planner.snap_forward_on_lockpick()
    assert planner.waypoint_index == 6  # dining_return_105
    # Forward-only: already past lockpick checkpoint → no snap.
    assert not planner.snap_forward_on_lockpick()
    assert planner.waypoint_index == 6


def test_lockpick_acquire_pays_checkpoint_via_snap() -> None:
    planner = _planner(start_index=3)
    progress = ProgressTracker()
    progress.seed_spawn_room("106")
    reward, bd = compute_reward(
        _state("106"),
        _state("106", inventory=["lockpick"], new_items=["lockpick"]),
        planner,
        progress=progress,
        graph=_graph(),
        rails_mode=True,
        return_breakdown=True,
    )
    assert bd["checkpoint_success"] == pytest.approx(RAILS_CHECKPOINT_REWARD)
    assert planner.waypoint_index == 6
    assert progress.checkpoint_success
    assert reward > RAILS_CHECKPOINT_REWARD - 0.01


def test_lockpick_key_items_rewarded_passes_without_inventory() -> None:
    progress = ProgressTracker()
    progress.key_items_rewarded.add("lockpick")
    planner = _planner(start_index=5)
    assert planner.advance_if_success(
        _state("106"),
        progress=progress,
        prev_state=_state("203"),
    )


def test_lockpick_checkpoint_latches_203_entry_until_delayed_pickup() -> None:
    planner = _planner(start_index=5)
    progress = ProgressTracker()

    assert not planner.advance_if_success(
        _state("106"),
        progress=progress,
        prev_state=_state("203"),
    )
    progress.note_leg_acquired("lockpick")
    assert planner.advance_if_success(
        _state("106", inventory=["lockpick"]),
        progress=progress,
        prev_state=_state("106"),
    )

    progress.on_waypoint_advanced()
    assert not progress.leg_room_transitions


def test_lockpick_checkpoint_does_not_latch_wrong_return_room() -> None:
    planner = _planner(start_index=5)
    progress = ProgressTracker()

    assert not planner.advance_if_success(
        _state("106"),
        progress=progress,
        prev_state=_state("201"),
    )
    progress.note_leg_acquired("lockpick")
    assert not planner.advance_if_success(
        _state("106", inventory=["lockpick"]),
        progress=progress,
        prev_state=_state("106"),
    )


def test_main_hall_ink_checkpoint_is_room_enter_only() -> None:
    planner = _planner(start_index=11)
    assert planner.advance_if_success(_state("106"), progress=ProgressTracker())


def test_save_100_checkpoint_is_room_enter_only() -> None:
    planner = _planner(start_index=31)
    assert planner.advance_if_success(_state("100"), progress=ProgressTracker())


def test_shotgun_rescue_requires_reentry_shotgun_and_ceiling_cutscene() -> None:
    prev = _state("116", inventory=["shotgun"])
    state = _state("115", inventory=["shotgun"])

    no_cutscene = _planner(start_index=17)
    assert not no_cutscene.advance_if_success(
        state,
        progress=ProgressTracker(),
        prev_state=prev,
    )

    no_shotgun = _planner(start_index=17)
    observed = ProgressTracker()
    observed.observe_cutscene("115:ceiling_lowering")
    assert not no_shotgun.advance_if_success(
        _state("115"),
        progress=observed,
        prev_state=prev,
    )

    rescued = _planner(start_index=17)
    assert rescued.advance_if_success(
        state,
        progress=observed,
        prev_state=prev,
    )


def test_goal_encodes_selected_one_leg_checkpoint() -> None:
    graph = _graph()
    encoder = ObsEncoder(ROOMS, graph, curriculum_stage_index=1)
    planner = _planner(start_index=51)
    state = _state("20D", x=1000, z=1000)
    goal = encoder.encode_goal(state, planner)
    assert planner.next_waypoint_room() == "20E"
    assert goal[GOAL_IDX["goal_room_index"]] == encoder._room_idx_norm("20E")
    assert goal[GOAL_IDX["doors_available"]] == 1.0
    assert goal[GOAL_IDX["waypoints_remaining"]] == pytest.approx(2 / 53)


def test_goal_appends_six_masked_checkpoint_semantic_slots() -> None:
    encoder = ObsEncoder(ROOMS, _graph(), curriculum_stage_index=1)
    planner = _planner(start_index=48)
    goal = encoder.encode_goal(
        _state("101", inventory=["shield_key", "shotgun"]),
        planner,
    )
    slots = goal[GOAL_BASE_DIM:].reshape(
        GOAL_LOOKAHEAD_SLOTS, GOAL_LOOKAHEAD_SLOT_DIM
    )
    assert slots[:, 0].tolist() == [1.0, 1.0, 1.0, 1.0, 1.0, 0.0]
    assert slots[0, 1] == encoder._room_idx_norm("101")
    assert slots[2, 3 + 1] == 1.0  # pickup
    assert slots[2, 12] > 0.0  # gained handgun bullets identity
    assert slots[3, 8] > 0.0  # required shield key identity
    assert slots[3, 11] == 1.0
    assert planner.peek_objective(2)["seq"] == 51
    assert planner.peek_waypoint_room(4) == "210"


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
    planner = _planner(start_index=47)
    assert planner.current_objective()["checkpoint_id"] == "yawn_box_prep_11B"
    assert "11B" in BOX_ROOMS

    active = _state("11B")
    active["lab_timer"] = 1
    assert not planner.advance_if_success(active, progress=ProgressTracker())

    expired = _state("11B")
    expired["lab_timer"] = 0
    assert planner.advance_if_success(expired, progress=ProgressTracker())


def test_richard_checkpoint_accepts_forced_settle_in_204() -> None:
    planner = _planner(start_index=43)
    progress = ProgressTracker()
    progress.observe_cutscene("20D:richard")
    settled = _state("204")
    assert planner.advance_if_success(settled, progress=progress)
    assert planner.current_objective()["checkpoint_id"] == "richard_forced_return_204"


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
    assert a["leg_span"] == 1


def test_chaining_curriculum_samples_bounded_remaining_span(tmp_path: Path) -> None:
    stage = {
        "route_id": "test",
        "cells_manifest": "missing.json",
        "route_steps": list(range(1, 5)),
        "legs_per_episode": 6,
    }
    opts = sample_one_leg_options(tmp_path, stage, rng=random.Random(1))
    assert opts["route_start_index"] == 0
    assert opts["leg_span"] == 4
    chaining = json.loads(
        (ROOT / "curriculum/yawn_rails_chaining.json").read_text(encoding="utf-8")
    )
    one_leg = json.loads(
        (ROOT / "curriculum/yawn_rails_one_leg.json").read_text(encoding="utf-8")
    )
    assert chaining["legs_per_episode"] == 6
    assert chaining["episode_mode"] == "multi_leg"
    assert chaining["route_id"] == "yawn_quest_v2"
    assert chaining["route_steps"][-1] == 53
    assert one_leg["episode_mode"] == "one_leg"
    assert one_leg["route_id"] == "yawn_quest_v2"
    assert one_leg["route_steps"][-1] == 53
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
        _state("11B") | {"inventory_slots": [("red_herb", 1)] * 8},
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
        cell = tmp_path / f"states/cp{idx}"
        cell.mkdir(parents=True)
        (cell / "cell.State").write_bytes(b"state")
        (cell / "cell.sidecar.json").write_text("{}", encoding="utf-8")
        cells.append({
            "checkpoint_index": 0,
            "state_path": f"states/cp{idx}/cell.State",
            "sidecar_path": f"states/cp{idx}/cell.sidecar.json",
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
    assert chosen["reset_source"] == "route_initial"


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
        {"checkpoint_success": RAILS_CHECKPOINT_REWARD},
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
    assert manifest["cells"][0]["inventory_feasible"] is True
    assert proposal["next_checkpoint_id"] == "kenneth_104"


def test_11b_almanac_has_chemical_but_not_square_crank() -> None:
    room_items = json.loads((ROOT / "data/room_items.json").read_text(encoding="utf-8"))
    names = {row["name"] for row in room_items["11B"]["items"]}
    assert "chemical" in names
    assert "square_crank" not in names
