"""Yawn-path milestone digest + v2 cell keys."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.milestone_digest import (
    DEFAULT_TILE_SPAN,
    KEY_ITEM_MILESTONES,
    VERIFIED_STORY_USES,
    YAWN_PATH_ROOMS,
    cell_key_v2,
    compute_digest,
    gallery_token,
    parse_cell_key_v2,
)
from re1_rl.pb_milestones import KEY_ITEM_MILESTONES as PB_KEYS
from re1_rl.pb_milestones import STORY_USE_MILESTONES
from re1_rl.progress import ProgressTracker


def test_yawn_path_rooms_match_plan() -> None:
    assert YAWN_PATH_ROOMS == frozenset(
        {
            "105",
            "104",
            "106",
            "107",
            "10F",
            "117",
            "118",
            "10C",
            "10D",
            "102",
            "116",
            "202",
            "203",
            "205",
            "209",
            "20E",
            "210",
        }
    )


def test_key_items_imported_from_pb() -> None:
    assert KEY_ITEM_MILESTONES is PB_KEYS
    assert "emblem" in KEY_ITEM_MILESTONES
    assert "shield_key" in KEY_ITEM_MILESTONES


def test_verified_story_uses() -> None:
    assert VERIFIED_STORY_USES == frozenset(STORY_USE_MILESTONES)
    assert VERIFIED_STORY_USES == frozenset(
        {
            "music_notes@10F_piano",
            "emblem@10F_alcove",
            "gold_emblem@105_fireplace",
        }
    )


def test_gallery_token_states() -> None:
    p = ProgressTracker()
    assert gallery_token(p) == "gallery:idle"

    p.gallery_step_index = 2
    assert gallery_token(p) == "gallery:step:2"

    p.gallery_needs_reentry = True
    assert gallery_token(p) == "gallery:retry_required"

    p.gallery_completed = True
    assert gallery_token(p) == "gallery:complete"


def test_compute_digest_example_shape() -> None:
    progress = ProgressTracker()
    progress.rewarded_story_uses.add("emblem@10F_alcove")
    progress.rewarded_cutscenes.add("104:0:s0")
    state = {
        "inventory": ["emblem", "beretta"],
        "inventory_slots": [["emblem", 1], ["beretta", 12]],
    }
    ever_held = {"lockpick", "emblem", "beretta"}
    digest = compute_digest(state, progress, ever_held=ever_held)
    assert digest == (
        "carry:emblem|got:emblem|got:lockpick|"
        "use:emblem@10F_alcove|event:kenneth_done|gallery:idle"
    )


def test_compute_digest_ignores_non_gate_inventory() -> None:
    progress = ProgressTracker()
    state = {"inventory": ["beretta", "handgun_bullets", "green_herb"]}
    digest = compute_digest(state, progress, ever_held={"beretta", "knife"})
    assert digest == "gallery:idle"
    assert "carry:" not in digest
    assert "got:" not in digest


def test_compute_digest_filters_unverified_uses() -> None:
    progress = ProgressTracker()
    progress.rewarded_story_uses.add("hex_crank@lab")
    progress.rewarded_story_uses.add("music_notes@10F_piano")
    digest = compute_digest({}, progress, ever_held=set())
    assert "use:music_notes@10F_piano" in digest
    assert "hex_crank" not in digest


def test_compute_digest_includes_weapons_progressed() -> None:
    progress = ProgressTracker()
    progress.weapons_progressed.add("bazooka_acid")
    digest = compute_digest({}, progress, ever_held=set())
    assert digest == "weapon:bazooka_acid|gallery:idle"


def test_cell_key_v2_roundtrip() -> None:
    digest = "carry:emblem|gallery:idle"
    key = cell_key_v2("20E", 9000, -100, digest, tile_span=DEFAULT_TILE_SPAN)
    assert key == f"v2|r=20E|x=2|z=-1|m={digest}"
    parsed = parse_cell_key_v2(key)
    assert parsed["room_id"] == "20E"
    assert parsed["tile_bin"] == (2, -1)
    assert parsed["milestone_digest"] == digest
    assert parsed["cell_key"] == key


def test_parse_cell_key_v2_rejects_v1() -> None:
    try:
        parse_cell_key_v2("105:3,1")
    except ValueError as exc:
        assert "not a v2" in str(exc)
    else:
        raise AssertionError("expected ValueError")
