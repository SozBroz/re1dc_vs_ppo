"""On-path ammo must be in the Muse packet and fail-closed if walked past."""
from __future__ import annotations

from re1_rl.phase1_route_council import (
    build_pass2_review_prompt,
    build_pass4_inventory_prompt,
    build_phase1_context,
    combat_strain_almanac,
    filtered_enemies,
    filtered_pickups,
    firearm_combat_kit,
    inventory_pressure_report,
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


def test_combat_strain_scales_handgun_rounds_by_four() -> None:
    strain = combat_strain_almanac()
    assert strain["handgun_damage"] == 4
    assert strain["species"]["zombie"] == {
        "handgun_rounds": 8,
        "hp": 32,
        "notes": "mansion zombie, average",
    }
    assert strain["species"]["dog"]["hp"] == 20
    assert strain["species"]["yawn"]["hp"] == 240
    assert strain["weapons"]["beretta"]["dmg"] == 4
    assert strain["weapons"]["beretta"]["rounds_to_kill"]["zombie"] == 8
    assert strain["weapons"]["shotgun"]["rounds_to_kill"]["zombie"] == 2
    assert strain["weapons"]["shotgun"]["rounds_to_kill"]["yawn"] == 12
    assert strain["on_path_ammo_relieves_strain"] is True


def test_phase1_enemies_include_hp() -> None:
    ctx = build_phase1_context("cp05")
    dogs = ctx["enemies"]["108"]["enemies"]
    assert any(row.get("type") == "dog" and row.get("hp") == 20 for row in dogs)
    z = ctx["enemies"]["104"]["enemies"]
    assert any(row.get("type") == "zombie" and row.get("handgun_rounds") == 8 for row in z)
    assert ctx["combat_strain"]["species"]["yawn"]["handgun_rounds"] == 60


def test_killed_dogs_drop_out_of_live_almanac() -> None:
    rooms = phase1_room_ids()
    live = filtered_enemies(rooms, killed={"108": {"dog": 2}})
    assert live["108"]["enemies"] == []
    assert live["108"]["killed"] == {"dog": 2}
    assert any(row.get("type") == "zombie" for row in live["10B"]["enemies"])
    ctx = build_phase1_context("cp05", enemies_killed={"108": {"dog": 2}})
    assert ctx["enemies_killed"] == {"108": {"dog": 2}}
    assert ctx["enemies"]["108"]["enemies"] == []
    leftover = filtered_enemies(rooms, killed={"108": {"dog": 1}})
    dogs = leftover["108"]["enemies"]
    assert len(dogs) == 1
    assert dogs[0]["type"] == "dog"
    assert dogs[0]["count"] == 1
    assert dogs[0]["catalog_count"] == 2


def test_10c_heal_rows_keep_plant_counts() -> None:
    pickups = filtered_pickups(phase1_room_ids(), taken=set())
    rows = {r["pickup_id"]: r for r in pickups["10C"]}
    assert rows["10C:green_herb:2"]["count"] == 4
    assert rows["10C:red_herb:3"]["count"] == 2


def test_pump_must_be_followed_by_armor_key() -> None:
    ctx = build_phase1_context("cp05")
    tip = dict(ctx["tip"])
    tip["pose"] = {"room_id": "10C"}
    tip["inventory"] = [{"slot": 1, "item": "chemical", "qty": 1}]
    ctx = {**ctx, "tip": tip}
    plan = {
        "beat_order": ["greenhouse_pump", "armor_key", "sun_crest", "place_sun_crest"],
        "next_chunk": {
            "end_anchor_beat_id": "greenhouse_pump",
            "steps": [
                {
                    "n": 1,
                    "op": "objective",
                    "room_id": "10C",
                    "site_id": "chemical@10C_greenhouse_pump",
                    "beat_id": "greenhouse_pump",
                }
            ],
        },
    }
    errors = "\n".join(validate_pass1_plan(plan, "cp05", ctx=ctx))
    assert "ARMOR_KEY_MUST_FOLLOW_PUMP" in errors


def test_inventory_pressure_flags_one_slot_into_10c() -> None:
    ctx = build_phase1_context("cp05")
    tip = dict(ctx["tip"])
    tip["pose"] = {"room_id": "118"}
    tip["known_taken_pickups"] = [
        "104:handgun_bullets:1",
        "104:handgun_bullets:2",
        "109:green_herb:1",
        "10B:green_herb:1",
        "118:chemical:1",
    ]
    ctx = {**ctx, "tip": tip}
    plan = {
        "leave_118": {
            "held_on_exit": [
                {"item": "knife", "qty": 0},
                {"item": "beretta", "qty": 0},
                {"item": "handgun_bullets", "qty": 30},
                {"item": "shield_key", "qty": 1},
                {"item": "handgun_bullets", "qty": 15},
                {"item": "green_herb", "qty": 1},
                {"item": "chemical", "qty": 1},
                {"item": None, "qty": 0},
            ]
        },
        "next_leg": {
            "end_anchor_beat_id": "greenhouse_pump",
            "steps": [
                {"n": 1, "op": "traverse", "edge_id": "118->10B"},
                {"n": 2, "op": "traverse", "edge_id": "10B->10A"},
                {"n": 3, "op": "traverse", "edge_id": "10A->109"},
                {"n": 4, "op": "traverse", "edge_id": "109->115"},
                {"n": 5, "op": "traverse", "edge_id": "115->116"},
                {"n": 6, "op": "acquire", "pickup_id": "116:shotgun:1"},
                {"n": 7, "op": "traverse", "edge_id": "116->106"},
                {"n": 8, "op": "traverse", "edge_id": "106->105"},
                {"n": 9, "op": "traverse", "edge_id": "105->104"},
                {"n": 10, "op": "traverse", "edge_id": "104->103"},
                {"n": 11, "op": "traverse", "edge_id": "103->10C"},
                {
                    "n": 12,
                    "op": "objective",
                    "site_id": "chemical@10C_greenhouse_pump",
                    "room_id": "10C",
                    "beat_id": "greenhouse_pump",
                },
            ],
        },
        "beat_order": ["greenhouse_pump", "armor_key", "sun_crest", "place_sun_crest"],
    }
    pressure = inventory_pressure_report(plan, ctx)
    rooms = {row["room_id"] for row in pressure["shortfalls"]}
    assert "10C" in rooms
    assert any(int(row["heal_units"]) >= 6 for row in pressure["shortfalls"])
    errors = "\n".join(validate_pass1_plan(plan, "cp05", ctx=ctx))
    assert "ARMOR_KEY_MUST_FOLLOW_PUMP" in errors
    assert "INVENTORY_SLOT_SHORTFALL" in errors
    assert "UNARMED_COMBAT_PATH" not in errors
    prompt = build_pass4_inventory_prompt(plan, ctx, code_errors=errors.splitlines())
    assert "shortfall" in prompt
    assert "armor_key" in prompt
    assert "suicide" in prompt.lower() or "unarmed" in prompt.lower()


def test_chemical_only_leave_is_unarmed_combat_path() -> None:
    ctx = build_phase1_context("cp05")
    tip = dict(ctx["tip"])
    tip["pose"] = {"room_id": "118"}
    tip["known_taken_pickups"] = [
        "104:handgun_bullets:1",
        "104:handgun_bullets:2",
        "109:green_herb:1",
        "10B:green_herb:1",
        "118:chemical:1",
    ]
    ctx = {**ctx, "tip": tip}
    plan = {
        "leave_118": {
            "held_on_exit": [
                {"item": "chemical", "qty": 1},
                {"item": None, "qty": 0},
                {"item": None, "qty": 0},
                {"item": None, "qty": 0},
                {"item": None, "qty": 0},
                {"item": None, "qty": 0},
                {"item": None, "qty": 0},
                {"item": None, "qty": 0},
            ]
        },
        "next_leg": {
            "end_anchor_beat_id": "armor_key",
            "steps": [
                {"n": 1, "op": "traverse", "edge_id": "118->10B"},
                {"n": 2, "op": "traverse", "edge_id": "10B->10A"},
                {"n": 3, "op": "traverse", "edge_id": "10A->109"},
                {"n": 4, "op": "traverse", "edge_id": "109->115"},
                {"n": 5, "op": "traverse", "edge_id": "115->116"},
                {"n": 6, "op": "acquire", "pickup_id": "116:shotgun:1"},
                {"n": 7, "op": "traverse", "edge_id": "116->106"},
                {"n": 8, "op": "traverse", "edge_id": "106->105"},
                {"n": 9, "op": "traverse", "edge_id": "105->104"},
                {"n": 10, "op": "traverse", "edge_id": "104->103"},
                {"n": 11, "op": "traverse", "edge_id": "103->10C"},
                {
                    "n": 12,
                    "op": "objective",
                    "site_id": "chemical@10C_greenhouse_pump",
                    "room_id": "10C",
                    "beat_id": "greenhouse_pump",
                },
                {"n": 13, "op": "acquire", "pickup_id": "10C:armor_key:1", "beat_id": "armor_key"},
            ],
        },
        "beat_order": ["greenhouse_pump", "armor_key", "sun_crest", "place_sun_crest"],
    }
    assert firearm_combat_kit(plan["leave_118"]["held_on_exit"]) is None
    errors = "\n".join(validate_pass1_plan(plan, "cp05", ctx=ctx))
    assert "UNARMED_COMBAT_PATH" in errors
    assert "10A" in errors or "115" in errors
