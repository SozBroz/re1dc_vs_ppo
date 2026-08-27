"""Phase 1 Route Council: mansion four-crest scope, LLM-authored plans, fail-closed validation.

Qwen authors beat order and every directed door hop. Code never invents or repairs hops.
"""
from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from typing import Any

from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import ITEM_IDS

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

ALLOWED_LOOT_CATEGORIES = frozenset({"ammo", "recovery", "key", "weapon"})
FORBIDDEN_LOOT_CATEGORIES = frozenset({"file", "misc", "unknown"})
# On-path freebies the plan must grab when leaving a visited room.
REQUIRED_ON_PATH_ITEMS = frozenset(
    {
        "handgun_bullets",
        "shotgun_shells",
        "green_herb",
        "red_herb",
        "blue_herb",
        "first_aid_spray",
        "first_aid_spray_alt",
    }
)
INVENTORY_SLOTS = 8
AMMO_STACK_CAP = {"handgun_bullets": 30, "shotgun_shells": 7}
CHEMICAL_PUMP_BEATS = frozenset({"greenhouse_pump"})
CHEMICAL_PUMP_SITES = frozenset({"chemical@10C_greenhouse_pump"})
FIREARMS = frozenset({"beretta", "shotgun"})
FIREARM_AMMO = {"beretta": "handgun_bullets", "shotgun": "shotgun_shells"}
# 10C bench plants are slot-pressure, not walk-past clips.
BULKY_ROOM_HEALS = frozenset({"10C"})


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _gates() -> dict[str, Any]:
    return load_json(DATA / "route_council_transition_gates.json")


def _dag() -> dict[str, Any]:
    return load_json(DATA / "story_dag_jill_standard_phase1.json")


def _categories() -> dict[str, str]:
    return load_json(DATA / "item_categories.json")


def phase1_room_ids() -> set[str]:
    rooms = load_json(DATA / "rooms.json")
    gates = _gates()
    excluded = {str(r) for r in gates.get("phase1_excluded_rooms") or []}
    out: set[str] = set()
    for room_id, row in rooms.items():
        if room_id.startswith("_") or not isinstance(row, dict):
            continue
        stage = int(row.get("stage") or 99)
        if stage <= 2 and room_id not in excluded:
            out.add(room_id)
    # Crest gate room is stage 1 and required.
    out.add("11A")
    out -= excluded
    return out


def key_requirements() -> dict[tuple[str, str], str]:
    affordances = load_json(DATA / "item_affordances.json")
    requirements: dict[tuple[str, str], str] = {}
    for item, entry in affordances.items():
        if not isinstance(entry, dict):
            continue
        for edge in entry.get("door_edges") or []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from_room") or "")
            target = str(edge.get("to_room") or "")
            if source and target:
                requirements[(source, target)] = canonical_item(item)
    return requirements


def directed_edges(valid_rooms: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    requirements = key_requirements()
    gates = _gates()
    quarantined = {(q["from"], q["to"]) for q in gates.get("quarantined_edges") or []}
    story_gates = gates.get("edge_gates") or {}
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for filename, trust in (("doors_rdt.json", "rdt"), ("doors_empirical.json", "empirical")):
        raw = load_json(DATA / filename)
        for key, entry in raw.items():
            if key.startswith("_") or not isinstance(entry, dict):
                continue
            source = str(entry.get("from_room") or "")
            target = str(entry.get("to_room") or "")
            if source not in valid_rooms or target not in valid_rooms:
                continue
            if (source, target) in quarantined:
                continue
            gate_meta = story_gates.get(f"{source}->{target}")
            if isinstance(gate_meta, dict) and gate_meta.get("phase1_policy") == "exclude_edge":
                continue
            if int(entry.get("door_x") or 0) == 0 and int(entry.get("door_z") or 0) == 0:
                continue
            row: dict[str, Any] = {
                "edge_id": f"{source}->{target}",
                "from": source,
                "to": target,
                "trust": trust,
            }
            required = requirements.get((source, target))
            if required:
                row["requires_key"] = required
            if isinstance(gate_meta, dict) and gate_meta.get("requires_story_uses"):
                row["requires_story_uses"] = list(gate_meta["requires_story_uses"])
            merged[(source, target)] = row
    return merged


def tip_snapshot(checkpoint: str) -> dict[str, Any]:
    # Local import keep scripts.build_route_council_prompt optional.
    import sys

    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import build_route_council_prompt as builder

    return builder.tip_snapshot(checkpoint)


def remaining_phase1_beats(done: set[str]) -> list[dict[str, Any]]:
    dag = _dag()
    goals = set(dag.get("goal_beat_ids") or [])
    beats = {b["id"]: b for b in dag.get("beats") or []}
    needed: set[str] = set()

    def add_closure(beat_id: str) -> None:
        if beat_id in needed or beat_id in done:
            return
        beat = beats.get(beat_id)
        if not beat:
            return
        needed.add(beat_id)
        for req in beat.get("requires") or []:
            add_closure(str(req))

    for goal in goals:
        add_closure(goal)
    # Drop frozen opening from "remaining work" list if done.
    remaining = [
        beats[bid]
        for bid in sorted(needed, key=lambda x: list(beats).index(x) if x in beats else 999)
        if bid not in done and beats[bid].get("chain") != "frozen"
    ]
    return remaining


def open_frontier(done: set[str], held: set[str]) -> list[dict[str, Any]]:
    """Beats whose requires are satisfied (done), still remaining."""
    remaining = remaining_phase1_beats(done)
    open_beats: list[dict[str, Any]] = []
    for beat in remaining:
        reqs = [str(r) for r in beat.get("requires") or []]
        if all(r in done for r in reqs):
            # Soft key hint: lockpick assumed held for gallery_enter.
            if beat["id"] == "gallery_enter" and "lockpick" not in held and "opening_lockpick" not in done:
                continue
            open_beats.append(beat)
    return open_beats


def cp05_done_beats() -> set[str]:
    dag = _dag()
    return set(dag.get("frozen_opening_beat_ids") or [])


def filtered_pickups(valid_rooms: set[str], taken: set[str]) -> dict[str, list[dict[str, Any]]]:
    raw = load_json(DATA / "room_items.json")
    categories = _categories()
    gates = _gates()
    excluded_items = {canonical_item(x) for x in gates.get("phase1_excluded_items") or []}
    rooms: dict[str, list[dict[str, Any]]] = {}
    for room_id, entry in raw.items():
        if room_id not in valid_rooms or not isinstance(entry, dict):
            continue
        rows: list[dict[str, Any]] = []
        for index, item in enumerate(entry.get("items") or [], start=1):
            name = canonical_item(str(item.get("name") or ""))
            if not name or name in excluded_items:
                continue
            cat = categories.get(name, "unknown")
            if cat in FORBIDDEN_LOOT_CATEGORIES and not item.get("key_item"):
                continue
            if cat not in ALLOWED_LOOT_CATEGORIES and not item.get("key_item"):
                continue
            pickup_id = f"{room_id}:{name}:{index}"
            if pickup_id in taken:
                continue
            if item.get("item_id") is None and cat == "ammo":
                # Keep catalog clips (108 L-passage) that alias to on-path ammo.
                if name not in REQUIRED_ON_PATH_ITEMS:
                    continue
            row = {
                "pickup_id": pickup_id,
                "item": name,
                "category": cat,
                "key_item": bool(item.get("key_item")),
                "count": max(1, int(item.get("count") or 1)),
            }
            if item.get("gate"):
                row["gate"] = item["gate"]
            notes = str(item.get("notes") or "")
            if notes:
                row["notes"] = notes
            if name == "handgun_bullets" and "15" in notes:
                row["known_qty_each"] = 15
            rows.append(row)
        if rows:
            rooms[room_id] = rows
    return rooms


def _species_row(enemy_type: str, species: dict[str, Any] | None = None) -> dict[str, Any]:
    table = species if species is not None else combat_strain_almanac()["species"]
    row = table.get(str(enemy_type or ""))
    if not isinstance(row, dict):
        return {}
    extra: dict[str, Any] = {}
    if row.get("hp") is not None:
        extra["hp"] = int(row["hp"])
    if row.get("handgun_rounds") is not None:
        extra["handgun_rounds"] = int(row["handgun_rounds"])
    return extra


def _annotate_enemy(enemy: dict[str, Any], species: dict[str, Any]) -> dict[str, Any]:
    etype = enemy.get("enemy_type") or enemy.get("type")
    row = {
        "type": etype,
        "count": enemy.get("count"),
        "spawn": enemy.get("spawn") or enemy.get("spawn_trigger") or "unknown",
    }
    row.update(_species_row(str(etype or ""), species))
    return row


def combat_strain_almanac() -> dict[str, Any]:
    """Species HP (handgun_rounds × 4) plus weapon damage for Muse strain math."""
    raw = load_json(DATA / "combat_strain.json")
    handgun_dmg = int(raw.get("handgun_damage") or 4)
    species: dict[str, Any] = {}
    for name, entry in (raw.get("species") or {}).items():
        if not isinstance(entry, dict):
            continue
        rounds = int(entry.get("handgun_rounds") or 0)
        species[str(name)] = {
            "handgun_rounds": rounds,
            "hp": rounds * handgun_dmg,
            "notes": entry.get("notes"),
        }
    from re1_rl.memory_map import ITEM_IDS
    from re1_rl.weapon_damage import WEAPON_NOMINAL_DAMAGE

    ammo_for = {
        "beretta": "handgun_bullets",
        "shotgun": "shotgun_shells",
        "colt_python": "magnum_rounds",
        "colt_python_dumdum": "dumdum_rounds",
        "bazooka_acid": "acid_rounds",
        "bazooka_explosive": "explosive_rounds",
        "bazooka_flame": "flame_rounds",
    }
    weapons: dict[str, Any] = {}
    for item_id, (lo, hi) in WEAPON_NOMINAL_DAMAGE.items():
        name = ITEM_IDS.get(int(item_id))
        if not name:
            continue
        avg = (int(lo) + int(hi)) / 2.0
        if avg <= 0:
            continue
        row: dict[str, Any] = {
            "dmg": avg,
            "dmg_min": int(lo),
            "dmg_max": int(hi),
        }
        ammo = ammo_for.get(name)
        if ammo:
            row["ammo"] = ammo
        kills: dict[str, int] = {}
        for sname, srow in species.items():
            hp = int(srow["hp"])
            kills[sname] = int(math.ceil(hp / avg))
        row["rounds_to_kill"] = kills
        weapons[name] = row
    return {
        "handgun_damage": handgun_dmg,
        "notes": raw.get("notes"),
        "species": species,
        "weapons": weapons,
        "on_path_ammo_relieves_strain": True,
        "typical_ammo_pickup_qty": {"handgun_bullets": 15, "shotgun_shells": 7},
    }


def filtered_enemies(
    valid_rooms: set[str],
    *,
    killed: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    raw = load_json(DATA / "room_enemies.json")
    overrides = load_json(DATA / "route_council_truth_overrides.json")
    species = combat_strain_almanac()["species"]
    dead = killed or {}
    rooms: dict[str, Any] = {}
    for room_id, entry in raw.items():
        if room_id not in valid_rooms or not isinstance(entry, dict):
            continue
        enemies = [_annotate_enemy(enemy, species) for enemy in (entry.get("enemies") or [])]
        rooms[room_id] = {"name": entry.get("room_name", ""), "enemies": enemies}
    for room_id, override in (overrides.get("enemy_rosters") or {}).items():
        if room_id not in valid_rooms:
            continue
        rooms[room_id] = {
            "name": override.get("room_name", ""),
            "enemies": [
                _annotate_enemy(e, species) for e in (override.get("enemies") or [])
            ],
            "authority": override.get("authority"),
        }
    return _subtract_killed_enemies(rooms, dead)


def _subtract_killed_enemies(
    rooms: dict[str, Any],
    killed: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Drop catalog rows that this lineage already killed. Remainder are still live."""
    for room_id, block in rooms.items():
        dead = killed.get(str(room_id).upper()) or killed.get(str(room_id)) or {}
        live: list[dict[str, Any]] = []
        for row in block.get("enemies") or []:
            etype = str(row.get("type") or "")
            catalog = int(row.get("count") or 0)
            taken = int(dead.get(etype) or 0)
            left = max(0, catalog - taken)
            annotated = dict(row)
            annotated["catalog_count"] = catalog
            annotated["killed"] = min(taken, catalog)
            annotated["count"] = left
            if left > 0:
                live.append(annotated)
        block["enemies"] = live
        leftover = {k: int(v) for k, v in dead.items() if int(v) > 0}
        if leftover:
            block["killed"] = leftover
    return rooms


def story_sites(valid_rooms: set[str]) -> list[dict[str, Any]]:
    raw = load_json(DATA / "story_item_use_sites.json")
    out = []
    for site in raw.get("sites") or []:
        room = str(site.get("room") or "")
        if room and room not in valid_rooms:
            continue
        out.append(
            {
                "site_id": site.get("id"),
                "item": canonical_item(str(site.get("item") or "")),
                "room": room,
                "consumes": bool(site.get("consumes")),
                "verified": not bool(site.get("_draft")),
            }
        )
    return out


def build_phase1_context(
    checkpoint: str = "cp05",
    *,
    enemies_killed: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    rooms = phase1_room_ids()
    tip = tip_snapshot(checkpoint)
    done = cp05_done_beats()
    held = {str(x) for x in tip.get("planner_assumed_held") or []}
    for row in tip.get("inventory") or []:
        if row.get("item"):
            held.add(str(row["item"]))
    taken = set(tip.get("known_taken_pickups") or [])
    edges = directed_edges(rooms)
    remaining = remaining_phase1_beats(done)
    frontier = open_frontier(done, held)
    rooms_raw = load_json(DATA / "rooms.json")
    room_rows = [
        {
            "id": rid,
            "name": (rooms_raw.get(rid) or {}).get("name", ""),
            "stage": (rooms_raw.get(rid) or {}).get("stage"),
        }
        for rid in sorted(rooms)
        if rid in rooms_raw
    ]
    return {
        "scope": "phase1_four_crests_mansion",
        "goal": "Place star, wind, sun, and moon crests at 11A crest slots.",
        "tip": tip,
        "done_beat_ids": sorted(done),
        "remaining_mandatory_beats": remaining,
        "open_frontier_beats": frontier,
        "rooms": room_rows,
        "directed_edges": sorted(edges.values(), key=lambda r: (r["from"], r["to"])),
        "pickups_allowed": filtered_pickups(rooms, taken),
        "enemies": filtered_enemies(rooms, killed=enemies_killed),
        "enemies_killed": {
            str(room).upper(): dict(types)
            for room, types in (enemies_killed or {}).items()
            if types
        },
        "combat_strain": combat_strain_almanac(),
        "story_use_sites": story_sites(rooms),
        "policy": {
            "llm_authors_every_hop": True,
            "code_never_repairs_hops": True,
            "excluded": _gates().get("phase1_excluded_rooms"),
            "excluded_items": _gates().get("phase1_excluded_items"),
            "no_maps_ink_files": True,
            "on_path_ammo_heals_required": True,
            "armor_key_immediately_after_pump": True,
            "chunk_ends_on_anchor_completion": True,
            "box_rooms_in_scope": ["100", "118"],
            "no_unarmed_combat_path": True,
            "knife_is_not_a_combat_kit": True,
            "combat_kit": "firearm (beretta or shotgun) plus matching ammo",
            "combat_strain": (
                "Sum enemy hp on your hops, divide by the carried weapon dmg, "
                "then subtract on-path ammo pickups you acquire. "
                "enemies[] already omits kills in enemies_killed; "
                "unkilled return_visit foes are still in the room. "
                "Do not bank the last gun to make herb slots — return to 118."
            ),
        },
    }


PASS1_SYSTEM = """You are the Phase 1 Route Council for Resident Evil Director's Cut
(SLUS-00551), Jill, Standard, any-percent glitchless.

You AUTHOR the route. Code will VALIDATE it fail-closed and will NOT invent,
repair, or shortest-path your door hops.

SCOPE: Phase 1 only — obtain and PLACE all four crests (star, wind, sun, moon)
at room 11A. Do not plan courtyard exit, square crank, residence, or later phases.

YOU MUST:
- Choose an order for remaining mandatory beats (parallel chains may interleave).
- Emit EVERY directed room hop yourself as traverse steps using edge_id from the
  supplied directed_edges list only.
- Emit acquire / objective / do_puzzle / trigger_cutscene / boss / go_to_box /
  use_box steps for mandatory work. If she is not already in a box room, emit
  go_to_box (room_id 118 or 100) BEFORE use_box so the policy gets a navigate
  compass to the box. use_box then rearranges to held_on_exit.
- Start from the tip room (Main Hall 106 after Barry lockpick).
- For the shield_key chain: after acquiring gold_emblem in 10F you MUST emit an
  objective step for emblem@10F_alcove (beat_id emblem_swap_alcove) to place the
  wooden emblem back BEFORE leaving for the dining fireplace. Skipping the alcove
  swap is invalid.
- For THIS response, author ONE next chunk that completes exactly one end_anchor
  beat (e.g. shield_key). The FINAL step of next_chunk.steps MUST be the step that
  completes that beat (usually acquire of the key item). Do NOT add trailing
  traverse steps after the anchor is done.
- ON-PATH FREE AMMO/HEALS: whenever your route ENTERS a room, you MUST emit
  acquire steps for every remaining pickups_allowed row there for handgun_bullets
  (catalog name may be clip), shotgun_shells, or healing herbs/sprays BEFORE
  leaving. Example: tea room 104 has two Kenneth clips; L-passage 108 has one
  clip — pick them when you pass through. Do not grab optional weapon ammo
  (acid/flame/magnum) unless it is your explicit goal. Those ammo pickups
  reduce combat strain (see combat_strain): zombie ≈ 8 handgun rounds (HP 32),
  dog ≈ 5 (HP 20), Yawn ≈ 60 (HP 240). Handgun deals 4; shotgun 15–25. Size
  the leave-box loadout for the remaining deficit after on-path pickups.
  enemies[] is the LIVE remainder after enemies_killed. Unkilled hallway
  zombies are still there on return.
- After greenhouse_pump (chemical@10C_greenhouse_pump) the NEXT step MUST be
  acquire 10C:armor_key:1. End that chunk on armor_key, not the pump.
  10C herbs are ungated — pick what fits AFTER reserving a combat kit,
  then pump, then the key.
- Size leave_118 free slots for physical loot counts in rooms you enter
  (10C is 4 green + 2 red, each herb is its own slot). Shotgun and
  armor_key each take a slot. Bank shield_key; it only opens the attic.
  If heals outnumber free slots after the combat kit, return to 118 or
  pick only what fits — never bank the last firearm and its ammo.
- COMBAT KIT: entering a room that still has live enemies[] requires a
  firearm (beretta or shotgun) plus matching ammo. Knife is not a kit.
  Empty shotgun with no shells is not a kit. 115/116 are live zombie
  rooms — do not suicide-run them unarmed.
- Also give full beat_order for the rest of Phase 1 (beat ids only).
- Assume lockpick is held when tip.planner_assumed_held says so.
- PPO handles combat; do not emit fight steps or coordinates or puzzle button
  sequences.

YOU MUST NOT:
- Invent rooms, edges, pickups, or items not in the packet.
- Target 11B, square_crank, maps, ink ribbons, or serum.
- Collapse multi-room travel into one step.
- Ask code to fill gaps between rooms.
- End the chunk by walking away after the anchor pickup — stop on the acquire.

Return ONE JSON object only (optional <think> before it)."""


PASS1_CONTRACT = {
    "beat_order": ["remaining Phase 1 beat ids in your preferred full order"],
    "next_chunk": {
        "why": "string",
        "end_anchor_beat_id": "beat completed by the final step",
        "steps": [
            {
                "n": 1,
                "id": "s1",
                "op": "traverse|acquire|objective|do_puzzle|trigger_cutscene|boss|go_to_box|use_box",
                "edge_id": "from->to when op=traverse else null",
                "pickup_id": "room:item:index when op=acquire else null",
                "site_id": "story site when op=objective else null",
                "beat_id": "DAG beat id when this step completes a beat else null",
                "room_id": "room for non-traverse ops",
                "note": "short",
            }
        ],
    },
    "truth_defects": [],
}


def build_pass1_prompt(
    checkpoint: str = "cp05",
    *,
    enemies_killed: dict[str, dict[str, int]] | None = None,
) -> str:
    ctx = build_phase1_context(checkpoint, enemies_killed=enemies_killed)
    # Drop bulky tip conflicts detail noise for model; keep essentials.
    tip = dict(ctx["tip"])
    tip.pop("truth_conflicts", None)
    tip.pop("leg_kills_by_room", None)
    packet = {
        "goal": ctx["goal"],
        "tip": tip,
        "done_beat_ids": ctx["done_beat_ids"],
        "open_frontier_beats": ctx["open_frontier_beats"],
        "remaining_mandatory_beats": [
            {
                "id": b["id"],
                "type": b["type"],
                "room_id": b.get("room_id"),
                "item_or_site": b.get("item_or_site"),
                "requires": b.get("requires"),
                "chain": b.get("chain"),
            }
            for b in ctx["remaining_mandatory_beats"]
        ],
        "rooms": ctx["rooms"],
        "directed_edges": ctx["directed_edges"],
        "pickups_allowed": ctx["pickups_allowed"],
        "enemies": ctx["enemies"],
        "enemies_killed": ctx.get("enemies_killed") or {},
        "combat_strain": ctx["combat_strain"],
        "story_use_sites": ctx["story_use_sites"],
        "policy": ctx["policy"],
        "output_contract": PASS1_CONTRACT,
    }
    sections = [
        ("ROLE: system", PASS1_SYSTEM),
        (
            "ROLE: user",
            "/think\n"
            "From Main Hall after Barry gave the lockpick, author one next chunk that "
            "completes shield_key. Include BOTH tea-room Kenneth clips when routing "
            "through 104. Prefer 104->10F (lockpick) into the Bar. After gold_emblem, "
            "MUST objective emblem@10F_alcove (emblem_swap_alcove) before the dining "
            "fireplace. Final step MUST be shield_key acquire — no walking afterward. "
            "Also emit full remaining beat_order to four crest placements at 11A. JSON only.",
        ),
        ("PHASE1_PACKET", compact(packet)),
    ]
    sep = "\n\n" + "=" * 88 + "\n"
    return sep.join(f"{title}\n{'-' * len(title)}\n{body}" for title, body in sections) + "\n"


def validate_pass1_plan(
    plan: dict[str, Any],
    checkpoint: str = "cp05",
    ctx: dict[str, Any] | None = None,
) -> list[str]:
    """Fail-closed sequential validation. Returns human-readable errors (first-failure style list)."""
    ctx = ctx if ctx is not None else build_phase1_context(checkpoint)
    if plan.get("leave_118"):
        tip = dict(ctx.get("tip") or {})
        tip["leave_118"] = plan["leave_118"]
        if (plan["leave_118"] or {}).get("held_on_exit"):
            tip["inventory"] = plan["leave_118"]["held_on_exit"]
        ctx = {**ctx, "tip": tip, "leave_118": plan["leave_118"]}
    errors: list[str] = []
    edges = {e["edge_id"]: e for e in ctx["directed_edges"]}
    tip_room = str((ctx["tip"].get("pose") or {}).get("room_id") or "106")
    held = {str(x) for x in ctx["tip"].get("planner_assumed_held") or []}
    for row in ctx["tip"].get("inventory") or []:
        if row.get("item"):
            held.add(str(row["item"]))
    room = tip_room
    story_uses = set(ctx["tip"].get("story_uses_done") or [])
    done = set(ctx["done_beat_ids"])
    taken = set(ctx["tip"].get("known_taken_pickups") or [])
    pickups_by_room: dict[str, list[dict[str, Any]]] = ctx["pickups_allowed"] or {}
    pickup_index = {
        p["pickup_id"]: p for rows in pickups_by_room.values() for p in rows
    }
    chunk = plan.get("next_chunk") or {}
    steps, end_anchor = walk_steps(plan)
    if not isinstance(steps, list) or not steps:
        return ["next_chunk.steps missing or empty"]
    if not end_anchor:
        return ["next_chunk.end_anchor_beat_id missing"]

    def leftover_freebees(room_id: str) -> list[str]:
        missed = []
        for row in pickups_by_room.get(room_id) or []:
            if row["pickup_id"] in taken:
                continue
            if row.get("gate"):
                continue
            if row.get("item") not in REQUIRED_ON_PATH_ITEMS:
                continue
            if room_id in BULKY_ROOM_HEALS and row.get("item") not in AMMO_STACK_CAP:
                continue
            missed.append(row["pickup_id"])
        return missed

    visited_for_freebees: set[str] = set()
    anchor_completed_at: int | None = None
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append("step is not an object")
            break
        op = str(step.get("op") or "")
        step_id = str(step.get("id") or f"n{step.get('n') or index + 1}")
        if op == "traverse":
            if room in visited_for_freebees:
                missed = leftover_freebees(room)
                if missed:
                    errors.append(
                        f"{step_id}: ON_PATH_FREEBEE_SKIPPED leaving {room} without "
                        f"acquiring {missed}"
                    )
                    break
            edge_id = str(step.get("edge_id") or "")
            edge = edges.get(edge_id)
            if not edge:
                errors.append(
                    f"{step_id}: EDGE_MISSING {edge_id} (not in Phase 1 directed graph)"
                )
                break
            if edge["from"] != room:
                errors.append(
                    f"{step_id}: ROOM_MISMATCH traverse from {edge['from']} but currently in {room}"
                )
                break
            req_key = edge.get("requires_key")
            if req_key and req_key not in held:
                errors.append(
                    f"{step_id}: EDGE_GATE_UNSATISFIED {edge_id} needs {req_key}; held={sorted(held)}"
                )
                break
            for site in edge.get("requires_story_uses") or []:
                if site not in story_uses:
                    errors.append(
                        f"{step_id}: EDGE_STORY_GATE_UNSATISFIED {edge_id} needs {site}"
                    )
                    break
            if errors:
                break
            room = edge["to"]
            visited_for_freebees.add(room)
            continue

        if op == "acquire":
            pickup_id = str(step.get("pickup_id") or "")
            row = pickup_index.get(pickup_id)
            if not row:
                errors.append(f"{step_id}: PICKUP_UNKNOWN {pickup_id}")
                break
            pref = pickup_id.split(":", 1)[0]
            if pref != room:
                errors.append(f"{step_id}: PICKUP_WRONG_ROOM {pickup_id} while in {room}")
                break
            if pickup_id in taken:
                errors.append(f"{step_id}: PICKUP_ALREADY_TAKEN {pickup_id}")
                break
            taken.add(pickup_id)
            held.add(row["item"])
            beat_id = str(step.get("beat_id") or "") or (
                end_anchor if row["item"] == end_anchor else ""
            )
            if beat_id:
                done.add(beat_id)
            if row["item"] == end_anchor or beat_id == end_anchor:
                anchor_completed_at = index
            continue

        if op == "go_to_box":
            dest = str(step.get("room_id") or "118").strip().upper() or "118"
            room = dest
            continue

        if op in {"objective", "use_key_item", "do_puzzle", "trigger_cutscene", "boss", "use_box"}:
            need_room = str(step.get("room_id") or "")
            if need_room and need_room != room:
                errors.append(
                    f"{step_id}: OP_WRONG_ROOM {op} wants {need_room} but in {room}"
                )
                break
            site = step.get("site_id")
            if site:
                story_uses.add(str(site))
                if str(site) == "emblem@10F_alcove":
                    done.add("emblem_swap_alcove")
            beat_id = str(step.get("beat_id") or "")
            if beat_id:
                done.add(beat_id)
            if beat_id == "gold_emblem_fireplace" or str(site) == "gold_emblem@105_fireplace":
                if "emblem_swap_alcove" not in done:
                    errors.append(
                        f"{step_id}: MISSING_EMBLEM_SWAP need objective "
                        "emblem@10F_alcove (emblem_swap_alcove) before fireplace"
                    )
                    break
            if beat_id in CHEMICAL_PUMP_BEATS or str(site) in CHEMICAL_PUMP_SITES:
                nxt = steps[index + 1] if index + 1 < len(steps) else None
                nxt_pick = str((nxt or {}).get("pickup_id") or "")
                nxt_beat = str((nxt or {}).get("beat_id") or "")
                if not nxt or str((nxt or {}).get("op") or "") != "acquire" or (
                    "armor_key" not in nxt_pick and nxt_beat != "armor_key"
                ):
                    errors.append(
                        f"{step_id}: ARMOR_KEY_MUST_FOLLOW_PUMP next step must "
                        "acquire 10C:armor_key:1 immediately after greenhouse_pump"
                    )
                    break
            if beat_id == end_anchor:
                anchor_completed_at = index
            continue

        errors.append(f"{step_id}: UNKNOWN_OP {op}")
        break

    if room in visited_for_freebees:
        missed = leftover_freebees(room)
        if missed:
            errors.append(
                f"ON_PATH_FREEBEE_SKIPPED ending in {room} without acquiring {missed}"
            )

    pressure = inventory_pressure_report(plan, ctx)
    inv_errors = [
        (
            f"INVENTORY_SLOT_SHORTFALL room={row['room_id']} "
            f"heals={row['heal_units']} free={row['free_slots']} "
            f"need={row['shortfall']} more empty slots "
            f"({', '.join(row.get('loot') or [])})"
        )
        for row in pressure.get("shortfalls") or []
    ]
    inv_errors.extend(
        (
            f"UNARMED_COMBAT_PATH enter {row['room_id']} live={row.get('foes')} "
            f"held={row.get('held')} — keep beretta+handgun_bullets or "
            f"shotgun+shotgun_shells; knife is not a kit"
        )
        for row in pressure.get("unarmed_enters") or []
    )
    if errors:
        return errors + inv_errors

    if anchor_completed_at is None:
        errors.append(
            f"CHUNK_ANCHOR_NOT_COMPLETED end_anchor_beat_id={end_anchor} never finished"
        )
    elif anchor_completed_at != len(steps) - 1:
        errors.append(
            f"CHUNK_MUST_END_ON_ANCHOR end_anchor={end_anchor} completed at step "
            f"{anchor_completed_at + 1} but {len(steps) - anchor_completed_at - 1} "
            "trailing step(s) follow; stop immediately after the anchor acquire"
        )

    beat_order = plan.get("beat_order") or []
    if not isinstance(beat_order, list) or len(beat_order) < 4:
        errors.append("beat_order must list remaining Phase 1 beats through crest placement")

    return errors + inv_errors


def walk_steps(plan: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Prefer ``next_leg`` (box then walk) else ``next_chunk``."""
    leg = plan.get("next_leg") or {}
    chunk = plan.get("next_chunk") or {}
    if isinstance(leg, dict) and leg.get("steps"):
        return list(leg.get("steps") or []), str(leg.get("end_anchor_beat_id") or "")
    return list(chunk.get("steps") or []), str(chunk.get("end_anchor_beat_id") or "")


def _tip_slots(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    leave = (ctx.get("tip") or {}).get("leave_118") or ctx.get("leave_118") or {}
    raw = leave.get("held_on_exit") or (ctx.get("tip") or {}).get("inventory") or []
    slots: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = row.get("item")
        slots.append(
            {
                "item": str(name) if name else None,
                "qty": int(row.get("qty") or 0),
            }
        )
    while len(slots) < INVENTORY_SLOTS:
        slots.append({"item": None, "qty": 0})
    return slots[:INVENTORY_SLOTS]


def _free_slots(slots: list[dict[str, Any]]) -> int:
    return sum(1 for row in slots if not row.get("item"))


def _add_to_slots(slots: list[dict[str, Any]], name: str, qty: int) -> int:
    """Put ``qty`` units of ``name`` into slots. Returns units that did not fit."""
    remaining = max(1, int(qty))
    cap = AMMO_STACK_CAP.get(name)
    if cap:
        for row in slots:
            if row.get("item") != name:
                continue
            space = cap - int(row.get("qty") or 0)
            if space <= 0:
                continue
            take = min(space, remaining)
            row["qty"] = int(row.get("qty") or 0) + take
            remaining -= take
            if remaining <= 0:
                return 0
    units = remaining if cap else max(1, int(qty))
    leftover = 0
    for _ in range(units):
        empty = next((row for row in slots if not row.get("item")), None)
        if empty is None:
            leftover += 1
            continue
        empty["item"] = name
        empty["qty"] = min(cap, qty) if cap else 1
    return leftover


def _consume_named(slots: list[dict[str, Any]], name: str) -> None:
    for row in slots:
        if row.get("item") == name:
            row["item"] = None
            row["qty"] = 0
            return


def _ammo_qty(slots: list[dict[str, Any]], name: str) -> int:
    return sum(int(row.get("qty") or 0) for row in slots if row.get("item") == name)


def firearm_combat_kit(slots: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Beretta+handgun_bullets or shotgun+shells. Knife does not count."""
    held = {str(row.get("item")) for row in slots if row.get("item")}
    for weapon, ammo in FIREARM_AMMO.items():
        if weapon in held and weapon in FIREARMS and _ammo_qty(slots, ammo) > 0:
            return {
                "weapon": weapon,
                "ammo_item": ammo,
                "ammo": _ammo_qty(slots, ammo),
            }
    return None


def _live_foes(room_id: str, ctx: dict[str, Any]) -> list[str]:
    block = (ctx.get("enemies") or {}).get(room_id) or {}
    live: list[str] = []
    for row in block.get("enemies") or []:
        if not isinstance(row, dict):
            continue
        count = int(row.get("count") or 1)
        if count <= 0:
            continue
        live.append(f"{count}x{row.get('type') or 'enemy'}")
    return live


def inventory_pressure_report(plan: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Walk the training steps and flag rooms whose physical loot exceeds free slots."""
    steps, _anchor = walk_steps(plan)
    leave = plan.get("leave_118") or (ctx.get("tip") or {}).get("leave_118") or {}
    if leave.get("held_on_exit"):
        ctx = {**ctx, "leave_118": leave}
    slots = _tip_slots(ctx)
    pickups = ctx.get("pickups_allowed") or {}
    pickup_index = {
        p["pickup_id"]: p for rows in pickups.values() for p in rows
    }
    taken = set((ctx.get("tip") or {}).get("known_taken_pickups") or [])
    edges = {e["edge_id"]: e for e in ctx.get("directed_edges") or []}
    room = str(((ctx.get("tip") or {}).get("pose") or {}).get("room_id") or "")
    shortfalls: list[dict[str, Any]] = []
    unarmed: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []

    def room_heal_need(room_id: str) -> tuple[int, list[str]]:
        need = 0
        ids: list[str] = []
        for row in pickups.get(room_id) or []:
            if row.get("pickup_id") in taken or row.get("gate"):
                continue
            if row.get("item") not in REQUIRED_ON_PATH_ITEMS:
                continue
            n = int(row.get("count") or 1)
            if row.get("item") in AMMO_STACK_CAP:
                continue
            need += n
            ids.append(f"{row['pickup_id']}x{n}")
        return need, ids

    if room:
        need, ids = room_heal_need(room)
        free = _free_slots(slots)
        if need > 0 and free == 0:
            shortfalls.append(
                {
                    "when": "start",
                    "room_id": room,
                    "free_slots": free,
                    "heal_units": need,
                    "shortfall": need - free,
                    "loot": ids,
                }
            )
    for index, step in enumerate(steps):
        op = str(step.get("op") or "")
        if op == "traverse":
            edge = edges.get(str(step.get("edge_id") or ""))
            if edge:
                room = str(edge["to"])
                foes = _live_foes(room, ctx)
                kit = firearm_combat_kit(slots)
                if foes and kit is None:
                    unarmed.append(
                        {
                            "when": f"enter:{step.get('edge_id')}",
                            "room_id": room,
                            "foes": foes,
                            "held": [
                                r.get("item") for r in slots if r.get("item")
                            ],
                        }
                    )
                need, ids = room_heal_need(room)
                free = _free_slots(slots)
                if need > 0 and free == 0:
                    shortfalls.append(
                        {
                            "when": f"enter:{step.get('edge_id')}",
                            "room_id": room,
                            "free_slots": free,
                            "heal_units": need,
                            "shortfall": need - free,
                            "loot": ids,
                        }
                    )
            continue
        if op == "acquire":
            pid = str(step.get("pickup_id") or "")
            row = pickup_index.get(pid) or {}
            name = str(row.get("item") or pid.split(":")[1] if ":" in pid else "")
            qty = int(row.get("count") or 1)
            if name in AMMO_STACK_CAP:
                qty = int(row.get("known_qty_each") or qty)
            overflow = _add_to_slots(slots, name, qty)
            taken.add(pid)
            timeline.append(
                {
                    "n": step.get("n") or index + 1,
                    "op": op,
                    "item": name,
                    "qty": qty,
                    "free_after": _free_slots(slots),
                    "overflow": overflow,
                }
            )
            continue
        beat = str(step.get("beat_id") or "")
        site = str(step.get("site_id") or "")
        if beat in CHEMICAL_PUMP_BEATS or site in CHEMICAL_PUMP_SITES:
            _consume_named(slots, "chemical")
            timeline.append(
                {
                    "n": step.get("n") or index + 1,
                    "op": op,
                    "freed": "chemical",
                    "free_after": _free_slots(slots),
                }
            )
    return {
        "leave_118": leave,
        "held_on_exit": [
            {"item": r.get("item"), "qty": r.get("qty")} for r in _tip_slots(ctx)
        ],
        "free_slots_on_exit": _free_slots(_tip_slots(ctx)),
        "shortfalls": shortfalls,
        "unarmed_enters": unarmed,
        "acquire_timeline": timeline,
        "notes": (
            "10C catalog is 4 green_herb + 2 red_herb (6 slots) plus armor_key. "
            "Each herb plant occupies its own slot. Ammo stacks to cap. "
            "Combat kit (firearm+ammo) is reserved — do not strip it for herbs."
        ),
    }


def _ungated_on_path_rows(
    pickups_allowed: dict[str, list[dict[str, Any]]],
    room_id: str,
    taken: set[str],
) -> list[dict[str, Any]]:
    rows = []
    for row in pickups_allowed.get(room_id) or []:
        if row.get("pickup_id") in taken:
            continue
        if row.get("gate"):
            continue
        if row.get("item") in REQUIRED_ON_PATH_ITEMS:
            rows.append(row)
    return rows


def rooms_visited_by_chunk(plan: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    tip = str(((ctx.get("tip") or {}).get("pose") or {}).get("room_id") or "")
    edges = {e["edge_id"]: e for e in ctx.get("directed_edges") or []}
    room = tip
    seen: list[str] = [tip] if tip else []
    for step in walk_steps(plan)[0]:
        if str(step.get("op") or "") != "traverse":
            continue
        edge = edges.get(str(step.get("edge_id") or ""))
        if not edge:
            continue
        room = str(edge["to"])
        if room and room not in seen:
            seen.append(room)
    return seen


def loot_review_packet(plan: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Entered rooms + one-door neighbors and their remaining ungated freebees."""
    taken = set((ctx.get("tip") or {}).get("known_taken_pickups") or [])
    for step in walk_steps(plan)[0]:
        pickup_id = step.get("pickup_id")
        if pickup_id:
            taken.add(str(pickup_id))
    pickups = ctx.get("pickups_allowed") or {}
    entered = rooms_visited_by_chunk(plan, ctx)
    entered_set = set(entered)
    edges = ctx.get("directed_edges") or []
    adjacent: list[str] = []
    for edge in edges:
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if src in entered_set and dst and dst not in entered_set and dst not in adjacent:
            adjacent.append(dst)
    return {
        "rooms_entered": entered,
        "freebees_in_entered_rooms": {
            room: _ungated_on_path_rows(pickups, room, taken) for room in entered
            if _ungated_on_path_rows(pickups, room, taken)
        },
        "adjacent_rooms": adjacent,
        "freebees_in_adjacent_rooms": {
            room: _ungated_on_path_rows(pickups, room, taken) for room in adjacent
            if _ungated_on_path_rows(pickups, room, taken)
        },
        "edges_from_entered": [
            e
            for e in edges
            if str(e.get("from") or "") in entered_set
            or str(e.get("to") or "") in entered_set
        ],
    }


PASS2_SYSTEM = """You are reviewing a Phase 1 next_chunk for missed loot.

The authoring pass often walks past handgun clips and herbs. You MUST:
- Keep the same end_anchor_beat_id. Do not change the story goal.
- Insert acquire steps for every remaining ungated pickups_allowed freebee
  (handgun_bullets/clip, shotgun_shells, herbs/sprays) in rooms the draft
  already ENTERS, before leaving that room.
- Look at adjacent_rooms one door off the path. If an ungated handgun clip or
  heal is sitting there and a return edge exists, you MAY add a one-room
  detour (traverse in, acquire, traverse back) before continuing. Do not
  wander two rooms off path. Do not open key-gated doors you do not hold.
- Never invent pickup_id or edge_id values. Use only ids in this packet.
- Final step must still complete the end_anchor. No trailing walks after it.
- Return ONE JSON object: next_chunk (full corrected steps), beat_order
  (unchanged unless you must), review_notes (what you added and why)."""


def build_pass2_review_prompt(
    plan: dict[str, Any],
    ctx: dict[str, Any],
    *,
    code_errors: list[str] | None = None,
) -> str:
    chunk = dict(plan.get("next_chunk") or {})
    packet = {
        "draft_next_chunk": chunk,
        "beat_order": plan.get("beat_order") or [],
        "code_audit_errors": list(code_errors or []),
        "loot": loot_review_packet(plan, ctx),
        "output_contract": {
            **PASS1_CONTRACT,
            "review_notes": ["what you inserted and why"],
        },
    }
    user = (
        "/think\nReview this draft for missed on-path ammo/heals and one-door "
        "nearby goodies. Insert the missing acquire (and detour hops if needed). "
        "Keep the same end_anchor. JSON only."
    )
    sections = [
        ("ROLE: system", PASS2_SYSTEM),
        ("ROLE: user", user),
        ("LOOT_REVIEW_PACKET", compact(packet)),
    ]
    sep = "\n\n" + "=" * 88 + "\n"
    return sep.join(f"{title}\n{'-' * len(title)}\n{body}" for title, body in sections) + "\n"


PASS4_SYSTEM = """You are refining inventory for a Phase 1 box-then-walk plan.

The authoring pass undersized leave_118 or stripped the combat kit. Jill has
8 slots. Each 10C herb plant is its own slot (4 green + 2 red). Shotgun and
armor_key each take a slot. Chemical frees one slot at the pump. Ammo stacks
(handgun 30, shells 7). Shield_key only opens the attic — bank it.

COMBAT KIT (hard rule):
- 115 (trap, 2 zombies) and 116 (living room, 2 zombies) plus any other room
  still listed in enemies[] are live fights. Do NOT suicide-run them.
- leave_118 MUST keep a firearm (beretta or shotgun) AND matching ammo
  (at least one handgun clip of 15, or shells if the shotgun is loaded).
- Knife is not a combat kit. Empty shotgun with 0 shells is not a kit.
- NEVER bank the last gun and all ammo to make herb slots.

10C herbs vs kit:
- Reserve the kit first. Pick only as many bench plants as still fit.
- If you need more empty slots, return to 118 AFTER the shotgun and box
  again — keep chemical + shotgun + beretta + at least 15 bullets.
- Do not enter 10C with zero empty slots.

You MUST:
- Keep the same story goal. After greenhouse_pump, immediately acquire
  10C:armor_key:1. end_anchor_beat_id MUST be armor_key.
- Fix every UNARMED_COMBAT_PATH, INVENTORY_SLOT_SHORTFALL, and
  ARMOR_KEY_MUST_FOLLOW_PUMP.
- Never invent pickup_id or edge_id. Use only ids in this packet.
- Return ONE JSON object: leave_118, next_chunk (box step), next_leg (walk
  ending on armor_key), beat_order, review_notes.

YOU MUST NOT leave 118 with only chemical."""


def build_pass4_inventory_prompt(
    plan: dict[str, Any],
    ctx: dict[str, Any],
    *,
    code_errors: list[str] | None = None,
) -> str:
    packet = {
        "draft": {
            "leave_118": plan.get("leave_118"),
            "next_chunk": plan.get("next_chunk"),
            "next_leg": plan.get("next_leg"),
            "beat_order": plan.get("beat_order") or [],
        },
        "code_audit_errors": list(code_errors or []),
        "inventory_pressure": inventory_pressure_report(plan, ctx),
        "combat_kit_rule": (
            "firearm+matching ammo required before every live-enemy door; "
            "knife is not a kit; do not strip the kit for 10C herbs"
        ),
        "loot": loot_review_packet(
            {"next_chunk": {"steps": walk_steps(plan)[0]}},
            ctx,
        ),
        "policy": {
            "armor_key_immediately_after_pump": True,
            "10C_order": ["herbs that fit", "greenhouse_pump", "armor_key"],
            "no_unarmed_combat_path": True,
            "knife_is_not_a_combat_kit": True,
            "shield_key_is_attic_only": True,
            "herb_plants_are_separate_slots": True,
            "10C_heal_plants": {"green_herb": 4, "red_herb": 2},
        },
        "output_contract": {
            "leave_118": "8 slots held on 118 exit plus bank/withdraw",
            "next_chunk": "one use_box in 118 if still in the save room",
            "next_leg": "walk; 10C herbs, pump, armor_key; end on armor_key",
            "review_notes": ["what you changed and why"],
        },
    }
    user = (
        "/think\nRefine inventory. Keep a firearm+ammo through live rooms. "
        "Fix UNARMED_COMBAT_PATH, INVENTORY_SLOT_SHORTFALL, and "
        "ARMOR_KEY_MUST_FOLLOW_PUMP. Do not suicide-run unarmed. "
        "Bank shield_key. End on armor_key. JSON only."
    )
    sections = [
        ("ROLE: system", PASS4_SYSTEM),
        ("ROLE: user", user),
        ("INVENTORY_REVIEW_PACKET", compact(packet)),
    ]
    sep = "\n\n" + "=" * 88 + "\n"
    return sep.join(f"{title}\n{'-' * len(title)}\n{body}" for title, body in sections) + "\n"


PASS3_SYSTEM = """You are repairing a Phase 1 next_chunk that failed code audit.

Insert acquire steps for every pickup_id listed in code_audit_errors
(ON_PATH_FREEBEE_SKIPPED). Use only those ids and existing directed edges.
Keep the same end_anchor. Final step must still complete it. JSON only."""


def build_pass3_repair_prompt(
    plan: dict[str, Any],
    ctx: dict[str, Any],
    code_errors: list[str],
) -> str:
    packet = {
        "draft_next_chunk": plan.get("next_chunk") or {},
        "beat_order": plan.get("beat_order") or [],
        "code_audit_errors": list(code_errors),
        "loot": loot_review_packet(plan, ctx),
        "output_contract": PASS1_CONTRACT,
    }
    user = (
        "/think\nCode rejected this draft. Fix every ON_PATH_FREEBEE_SKIPPED "
        "by inserting the listed acquire steps before leaving that room. JSON only."
    )
    sections = [
        ("ROLE: system", PASS3_SYSTEM),
        ("ROLE: user", user),
        ("FREEBEE_REPAIR_PACKET", compact(packet)),
    ]
    sep = "\n\n" + "=" * 88 + "\n"
    return sep.join(f"{title}\n{'-' * len(title)}\n{body}" for title, body in sections) + "\n"


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4
