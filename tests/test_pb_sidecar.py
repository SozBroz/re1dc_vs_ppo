"""Unit tests for PB episode sidecar dump/apply."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.episode_history import EpisodeHistory
from re1_rl.item_todo import ItemTracker, build_item_todo
from re1_rl.pb_sidecar import (
    SIDECAR_SCHEMA_VERSION,
    EpisodeSidecarParts,
    SidecarSchemaError,
    apply_episode_sidecar,
    apply_history_sidecar,
    apply_item_tracker_sidecar,
    apply_progress_sidecar,
    dump_episode_sidecar,
    history_to_sidecar,
    item_tracker_to_sidecar,
    progress_to_sidecar,
)
from re1_rl.progress import ProgressTracker

ROUTE = Path(__file__).resolve().parents[1] / "data" / "route_jill_anypct.json"


def _sample_progress() -> ProgressTracker:
    p = ProgressTracker()
    p.seed_spawn_room("105")
    p.first_visit("105", at_waypoint=0)
    p.first_visit("10F", at_waypoint=1)
    p.claim_cutscene_bonus("dining_wesker")
    p.claim_story_use_bonus("bar_alcove_emblem")
    p.claim_document_examine_bonus("107")
    p.claim_weapon_progress("beretta")
    p.note_softlock_extension(7200)
    p.note_stagnation_step(made_progress=False, step_frames=120)
    p.note_pickup_cutscene_block("105")
    p.gallery_step_index = 2
    p.gallery_pending_reward = 4.0
    p.gallery_needs_reentry = True
    p._spawn_room_bonus_paid = True
    return p


def test_progress_round_trip() -> None:
    src = _sample_progress()
    restored = ProgressTracker()
    apply_progress_sidecar(restored, progress_to_sidecar(src))

    assert restored.visited_rooms == src.visited_rooms
    assert restored.rewarded_cutscenes == src.rewarded_cutscenes
    assert restored.rewarded_story_uses == src.rewarded_story_uses
    assert restored.rewarded_document_rooms == src.rewarded_document_rooms
    assert restored.cutscene_blocked_after_pickup_room == src.cutscene_blocked_after_pickup_room
    assert restored.kenneth_gate_breached == src.kenneth_gate_breached
    assert restored.spawn_room_id == src.spawn_room_id
    assert restored._spawn_room_bonus_paid == src._spawn_room_bonus_paid
    assert restored.weapons_progressed == src.weapons_progressed
    assert restored.softlock_cap_frames == src.softlock_cap_frames
    assert restored.stagnation_frames == src.stagnation_frames
    assert restored.gallery_step_index == src.gallery_step_index
    assert restored.gallery_pending_reward == src.gallery_pending_reward
    assert restored.gallery_completed == src.gallery_completed
    assert restored.gallery_needs_reentry == src.gallery_needs_reentry


def test_ever_held_round_trip() -> None:
    src = ItemTracker(todo=build_item_todo(ROUTE))
    src.update([("beretta", 15), ("wooden_emblem", 1)])
    src.update([("beretta", 15)])
    dst = ItemTracker(todo=build_item_todo(ROUTE))
    apply_item_tracker_sidecar(dst, item_tracker_to_sidecar(src))
    assert dst.ever_held == src.ever_held


def test_history_round_trip() -> None:
    src = EpisodeHistory()
    src.reset("100", step=0)
    src.on_step(
        {"room_id": "101", "step": 10},
        prev_state={"room_id": "100", "step": 9},
        new_items=["shield_key"],
    )
    src.on_step(
        {"room_id": "105", "step": 25},
        prev_state={"room_id": "101", "step": 20},
        new_items=["emblem"],
    )
    dst = EpisodeHistory()
    apply_history_sidecar(dst, history_to_sidecar(src))
    assert list(dst.room_deque.entries) == list(src.room_deque.entries)
    assert list(dst.acquisitions.entries) == list(src.acquisitions.entries)


def test_apply_prevents_reclaiming_rewards() -> None:
    src = _sample_progress()
    src.claim_cutscene_bonus("tea_corridor")
    src.claim_story_use_bonus("dining_fireplace_gold")
    src.first_visit("20E")

    parts = EpisodeSidecarParts(
        progress=ProgressTracker(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
    )
    data = dump_episode_sidecar(parts)
    data["progress"] = progress_to_sidecar(src)
    apply_episode_sidecar(parts, data)

    p = parts.progress
    assert p.claim_cutscene_bonus("dining_wesker") is False
    assert p.claim_cutscene_bonus("tea_corridor") is False
    assert p.claim_story_use_bonus("bar_alcove_emblem") is False
    assert p.claim_story_use_bonus("dining_fireplace_gold") is False
    assert p.claim_document_examine_bonus("107") is False
    assert p.first_visit("10F") is False
    assert p.first_visit("20E") is False
    assert p.claim_weapon_progress("beretta") is False
    assert p.claim_spawn_room_bonus() is False


def test_schema_version_mismatch_raises() -> None:
    parts = EpisodeSidecarParts(
        progress=ProgressTracker(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
    )
    data = dump_episode_sidecar(parts)
    data["schema_version"] = SIDECAR_SCHEMA_VERSION + 1
    with pytest.raises(SidecarSchemaError, match="schema_version"):
        apply_episode_sidecar(parts, data)


def test_full_dump_includes_metadata_and_box_cache() -> None:
    parts = EpisodeSidecarParts(
        progress=_sample_progress(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
        box_cache=[(0, 15), (3, 1)],
    )
    parts.items.ever_held = {"beretta", "emblem"}
    parts.episode_history.reset("105", step=0)

    data = dump_episode_sidecar(
        parts,
        captured_room_id="10F",
        captured_at_iso="2026-07-21T12:00:00+00:00",
    )
    assert data["schema_version"] == SIDECAR_SCHEMA_VERSION
    assert data["captured_room_id"] == "10F"
    assert data["box_cache"] == [[0, 15], [3, 1]]
    assert "waypoint" not in data
    assert "waypoint_index" not in data

    fresh = EpisodeSidecarParts(
        progress=ProgressTracker(),
        items=ItemTracker(todo=[]),
        episode_history=EpisodeHistory(),
    )
    apply_episode_sidecar(fresh, data)
    assert fresh.box_cache == [(0, 15), (3, 1)]
    assert fresh.items.ever_held == {"beretta", "emblem"}
