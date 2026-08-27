"""On-path ammo must be in the Muse packet and fail-closed if walked past."""
from __future__ import annotations

from re1_rl.phase1_route_council import (
    build_pass2_review_prompt,
    build_phase1_context,
    filtered_pickups,
    loot_review_packet,
    phase1_room_ids,
    validate_pass1_plan,
)


def test_108_clip_is_in_pickups_allowed() -> None:
    pickups = filtered_pickups(phase1_room_ids(), taken=set())
    ids = [row["pickup_id"] for row in pickups.get("108") or []]
    assert "108:handgun_bullets:1" in ids


def test_leaving_108_without_clip_fails_audit() -> None:
    ctx = build_phase1_context("cp05")
    plan = {
        "beat_order": ["chemical", "greenhouse_pump", "armor_key", "place_sun_crest"],
        "next_chunk": {
            "end_anchor_beat_id": "chemical",
            "steps": [
                {"n": 1, "op": "traverse", "edge_id": "106->107"},
                {"n": 2, "op": "traverse", "edge_id": "107->108"},
                {"n": 3, "op": "traverse", "edge_id": "108->109"},
                {"n": 4, "op": "acquire", "pickup_id": "118:chemical:1"},
            ],
        },
    }
    errors = validate_pass1_plan(plan, "cp05", ctx=ctx)
    joined = "\n".join(errors)
    assert "ON_PATH_FREEBEE_SKIPPED" in joined
    assert "108:handgun_bullets:1" in joined


def test_loot_review_flags_missed_108_and_lists_neighbors() -> None:
    ctx = build_phase1_context("cp05")
    plan = {
        "beat_order": ["a", "b", "c", "d"],
        "next_chunk": {
            "end_anchor_beat_id": "chemical",
            "steps": [
                {"n": 1, "op": "traverse", "edge_id": "106->107"},
                {"n": 2, "op": "traverse", "edge_id": "107->108"},
                {"n": 3, "op": "traverse", "edge_id": "108->109"},
            ],
        },
    }
    loot = loot_review_packet(plan, ctx)
    entered_ids = [
        row["pickup_id"]
        for rows in loot["freebees_in_entered_rooms"].values()
        for row in rows
    ]
    assert "108:handgun_bullets:1" in entered_ids
    prompt = build_pass2_review_prompt(plan, ctx, code_errors=["ON_PATH_FREEBEE_SKIPPED"])
    assert "108:handgun_bullets:1" in prompt
    assert "adjacent_rooms" in prompt
