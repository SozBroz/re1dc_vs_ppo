"""pl134 place_wind_crest tip: Muse next_chunk (Phase 1 after cp05 end-anchor)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_tmp"))
sys.path.insert(0, str(ROOT / "scripts"))

from pl29_muse_box import PASS1_SYSTEM, build_packet, compact  # noqa: E402
from pl95_muse_next import _slim_packet_for_coexist  # noqa: E402
from pl110_muse_next import PL110_DONE, PL110_TAKEN, SHOTGUN_RETURN  # noqa: E402
from probe_muse_phase1 import _extract_json, _split_prompt  # noqa: E402
from re1_rl.pb_sidecar import enemies_killed_from_sidecar  # noqa: E402
from re1_rl.phase1_route_council import (  # noqa: E402
    open_frontier,
    remaining_phase1_beats,
    validate_pass1_plan,
)

PROMPT_OUT = ROOT / "_tmp" / "pl134_muse_next_prompt.txt"
ALMANAC_OUT = ROOT / "_tmp" / "pl134_muse_next_almanac.json"
REQ_OUT = ROOT / "_tmp" / "pl134_muse_next_request.json"
RAW_OUT = ROOT / "_tmp" / "pl134_muse_next_raw.json"
PLAN_OUT = ROOT / "_tmp" / "pl134_muse_next_response.json"
WH3 = "sshuser@192.168.0.229"
WH3_REQ = r"C:\Users\sshuser\re1_rl\_tmp\pl134_muse_next_request.json"
WH3_RAW = r"C:\Users\sshuser\re1_rl\_tmp\pl134_muse_next_raw.json"

# 103->104 was unlocked during the place_wind resource walk — do NOT block it.
BLOCKED_EDGES: frozenset[str] = frozenset()

PL134_DONE = set(PL110_DONE) | {"place_wind_crest"}
PL134_TAKEN = set(PL110_TAKEN) | {
    "10E:handgun_bullets:1",
    "10E:shotgun_shells:2",
    "111:handgun_bullets:1",
    "111:shotgun_shells:2",
    "112:green_herb:1",
    "112:green_herb:2",
}

USER = """/think
Tip is planner-loyal pl134 in ROOFED PASSAGE (11A). Jill JUST completed
place_wind_crest (wind_crest@11A_crest_slot). cp05_shield_key chunk_final is
true. Author the NEXT training chunk from this cell (first mint after this tip
is the first success of your next_chunk).

Live tip (decoded from pl134 cell.pst MainRAM + sidecar box_cache):
- room 11A, HP 68 Fine, not poisoned
- inv: beretta(7 loaded), shotgun(4 loaded), armor_key, green_herb x1,
  4 empty slots. NO separate handgun_bullets / shotgun_shells stacks.
- box (100/118 shared cache): shield_key, mixed_herbs_gg, knife, acid_rounds x6
- star + sun + wind ALREADY PLACED at 11A. moon_crest NOT yet acquired.
- blue_jewel SPENT in tiger eye. chemical SPENT at greenhouse pump.
- shield_key is BOXED (needed for attic 20E/210). armor_key is HELD.
- Richard bleedout + place_sun already done earlier. tea 103->104 UNLOCKED.
- lockpick special slot assumed held (opening_lockpick done)

OPERATOR FACTS (fail closed — do not violate):
1) Do NOT re-do place_star / place_sun / place_wind / tiger / blue_jewel /
   push_statue / greenhouse_pump. Those beats are done.
2) 103->104 tea-room one-way is ALREADY OPEN. Do not claim it is locked.
3) Shotgun RETURN was already 116->115->109. Do not emit 116->106.
4) Only move items that already exist in tip.inventory or tip.box, PLUS new
   pickups you acquire on this walk from pickups_allowed.
5) YAWN AMMO FAIL-CLOSED: beretta 7 + shotgun 4 is NOT enough for yawn_1
   (~60 handgun-rounds / ~4 acid GL rounds in combat_strain). Do NOT walk
   into 210 for yawn with only that. Before attic/Yawn you MUST stage a real
   kit:
   a) GRENADE LAUNCHER: acquire 212:bazooka_acid:1 (Forest Speyer on terrace).
      Path uses 203->211->212 (and back). armor_key already held.
   b) Withdraw acid_rounds from box (already boxed x6) so GL has ammo.
      Bazooka without acid_rounds is not a kit. Acid alone without bazooka
      is not a kit.
   c) 208/209/20A RESOURCES (armor_key opens 207->208): enter deer hub 208,
      bedroom 209 (209:red_herb:1, 209:handgun_bullets:2, 209:lighter:3),
      study 20A (20A:explosive_rounds:1 via cabinet puzzle — emit acquire
      when you take it). Grab what still fits; lighter enables later candle
      puzzles if you keep it.
   d) 20D has TWO green_herb pickups — take both when you enter 20D if slots.
6) use_box MUST include held_on_exit (8 slots) naming every item she holds
   when the box closes — especially shield_key + bazooka_acid + acid_rounds
   for the Yawn approach. go_to_box before use_box if not already in 100/118.
   BOX FROM 11A FAIL-CLOSED: you are in 11A next to 10A/10B. Use box room
   118 via 11A->10A->10B->118 only. Do NOT walk the long 1F loop to box 100
   (no 10A->109->…->101->100). 100 and 118 share the same cache — 118 is
   mandatory from this tip. After the box, exit 118->10B toward 207 / terrace
   / deer wing / attic.
7) Open frontier after place_wind_crest: attic_enter, then yawn_intro /
   yawn_1 / moon_crest / place_moon_crest. This chunk's end_anchor_beat_id
   MUST match the FINAL step's completed beat. If you only stage the GL,
   you cannot claim attic_enter — keep walking until attic_enter (or
   yawn_1 / moon_crest) with GL+acid already held, OR invent no fake beat
   for bazooka (there is no DAG beat for 212:bazooka). Prefer end_anchor
   attic_enter only after: box withdraw (shield_key+acid_rounds),
   212:bazooka_acid:1, AND 208/209/20A loot (at least 209 handgun clip +
   20A explosive_rounds; lighter/red_herb if slots). Order may be
   box → terrace GL → deer wing → attic, or box → deer wing → terrace →
   attic; do not skip 208/209/20A.
8) Author every hop from supplied directed_edges only.
9) ON-PATH FREE AMMO/HEALS: when you ENTER a room, acquire remaining
   pickups_allowed handgun_bullets / shotgun_shells / herb/spray there
   before leaving (what still fits). 20D has TWO green_herb rows — take both.

AUTHOR from pl134 / 11A:
- next_chunk: stage Yawn kit via box + terrace GL + 208/209/20A loot, then
  either end on that staging beat or continue only with GL+acid held.
- beat_order for the rest of Phase 1 (through place_moon_crest).

JSON only:
{
  "why_tea_unlocked": "short note that 103->104 is already open",
  "why_not_naked_attic": "why beretta7+sg4 is insufficient for Yawn",
  "why_box_118": "why 118 not 100 from tip 11A",
  "kit_plan": "GL from 212, acid from box 118, 209/20A loot, shield_key withdraw",
  "next_chunk": { "why": "...", "end_anchor_beat_id": "...", "steps": [ ... ] },
  "beat_order": [ ... ]
}
"""


def _tip_pl134(base: dict) -> dict:
    tip = dict(base)
    tip["checkpoint"] = {
        "index": 134,
        "id": "cp05_shield_key_step129",
        "source": "states/planner_loyal/cells/pl134",
        "chunk_final": True,
        "next_mint": "pl135_first_success_of_next_chunk",
    }
    tip["pose"] = {
        "room_id": "11A",
        "hp": 68,
        "hp_band": "fine",
        "poison": False,
    }
    tip["inventory"] = [
        {"slot": 1, "item": "beretta", "qty": 7},
        {"slot": 2, "item": "shotgun", "qty": 4},
        {"slot": 3, "item": "armor_key", "qty": 1},
        {"slot": 4, "item": "green_herb", "qty": 1},
        {"slot": 5, "item": None, "qty": 0},
        {"slot": 6, "item": None, "qty": 0},
        {"slot": 7, "item": None, "qty": 0},
        {"slot": 8, "item": None, "qty": 0},
    ]
    tip["box"] = [
        {"slot": 0, "item": None, "qty": 0},
        {"slot": 1, "item": "shield_key", "qty": 1},
        {"slot": 2, "item": "mixed_herbs_gg", "qty": 1},
        {"slot": 3, "item": "knife", "qty": 0},
        {"slot": 4, "item": "acid_rounds", "qty": 6},
    ]
    tip["derived"] = {
        "ammo": {"handgun_bullets": 7, "shotgun_shells": 4, "acid_rounds": 6},
        "heals": {"green_herb": 1, "mixed_herbs_gg": 1},
        "free_slots": 4,
        "lockpick_in_ram": True,
        "lockpick_slot": "special",
        "weapons": ["beretta", "shotgun"],
        "combat_kit": True,
        "combat_kit_why": "beretta 7 loaded + shotgun 4 loaded; green_herb on person",
        "wind_crest_placed": True,
        "shield_key_boxed": True,
        "armor_key_held": True,
    }
    tip["planner_assumed_held"] = [
        "beretta",
        "shotgun",
        "armor_key",
        "green_herb",
        "lockpick",
        "shield_key",
        "mixed_herbs_gg",
        "knife",
        "acid_rounds",
    ]
    tip["ever_held"] = sorted(
        {
            "acid_rounds",
            "armor_key",
            "beretta",
            "blue_jewel",
            "chemical",
            "emblem",
            "first_aid_spray_alt",
            "gold_emblem",
            "green_herb",
            "handgun_bullets",
            "knife",
            "mixed_herbs_gg",
            "mixed_herbs_gr",
            "music_notes",
            "red_herb",
            "shield_key",
            "shotgun",
            "shotgun_shells",
            "star_crest",
            "sun_crest",
            "wind_crest",
        }
    )
    tip["visited_rooms"] = [
        "100",
        "101",
        "102",
        "103",
        "104",
        "105",
        "106",
        "107",
        "108",
        "109",
        "10A",
        "10B",
        "10C",
        "10D",
        "10E",
        "10F",
        "111",
        "112",
        "115",
        "116",
        "117",
        "118",
        "11A",
        "201",
        "202",
        "203",
        "204",
        "205",
        "207",
        "20D",
    ]
    tip["known_taken_pickups"] = sorted(PL134_TAKEN)
    tip["story_uses_done"] = [
        "music_notes@10F_piano",
        "emblem@10F_alcove",
        "gold_emblem@105_fireplace",
        "chemical@10C_greenhouse_pump",
        "star_crest@11A_crest_slot",
        "sun_crest@11A_crest_slot",
        "wind_crest@11A_crest_slot",
        "dining_statue_knocked",
        "blue_jewel@10D_tiger_eye",
    ]
    tip["affordances_note"] = (
        "star+sun+wind placed at 11A. shield_key boxed for attic. "
        "armor_key held. HP 68 Fine. tea 103->104 unlocked."
    )
    tip["door_graph_hint"] = {
        "11A_neighbors": ["10A"],
        "to_box_100": ["11A", "10A", "109", "108", "107", "106", "105", "101", "100"],
        "to_box_118": ["11A", "10A", "10B", "118"],
        "attic": "withdraw shield_key then 20E->210",
        "shotgun_return": SHOTGUN_RETURN,
        "tea_unlocked": "103->104",
    }
    return tip


def build_pl134_packet(*, slim: bool = False) -> dict:
    packet = build_packet()
    packet["tip"] = _tip_pl134(packet["tip"])
    packet["done_beat_ids"] = sorted(PL134_DONE)
    held = {
        "beretta",
        "shotgun",
        "armor_key",
        "green_herb",
        "lockpick",
        "shield_key",
        "mixed_herbs_gg",
        "knife",
        "acid_rounds",
    }
    packet["open_frontier_beats"] = [
        {
            "id": b["id"],
            "type": b["type"],
            "room_id": b.get("room_id"),
            "item_or_site": b.get("item_or_site"),
            "requires": b.get("requires"),
            "chain": b.get("chain"),
        }
        for b in open_frontier(PL134_DONE, held)
    ]
    packet["remaining_mandatory_beats"] = [
        {
            "id": b["id"],
            "type": b["type"],
            "room_id": b.get("room_id"),
            "item_or_site": b.get("item_or_site"),
            "requires": b.get("requires"),
            "chain": b.get("chain"),
        }
        for b in remaining_phase1_beats(PL134_DONE)
    ]
    taken = set(PL134_TAKEN)
    pickups = {}
    for room, rows in (packet.get("pickups_allowed") or {}).items():
        kept = [r for r in rows if r.get("pickup_id") not in taken]
        if kept:
            pickups[room] = kept
    packet["pickups_allowed"] = pickups
    packet["directed_edges"] = [
        e for e in (packet.get("directed_edges") or []) if e.get("edge_id") not in BLOCKED_EDGES
    ]
    sidecar_p = ROOT / "states" / "planner_loyal" / "cells" / "pl134" / "cell.sidecar.json"
    if sidecar_p.is_file():
        packet["enemies_killed"] = enemies_killed_from_sidecar(
            json.loads(sidecar_p.read_text(encoding="utf-8"))
        )
    contract = dict(packet.get("output_contract") or {})
    contract.pop("leave_118", None)
    packet["output_contract"] = contract
    packet["operator_locks"] = {
        "shotgun_return": SHOTGUN_RETURN,
        "blocked_edges": sorted(BLOCKED_EDGES),
        "tea_103_104": "unlocked",
        "tip_cell": "pl134",
        "chunk_final": True,
        "hp_fine": 68,
        "wind_crest_placed": True,
        "shield_key_boxed": True,
        "armor_key_held": True,
        "learner_running": False,
        "why": (
            "cp05_shield_key chunk ended on place_wind_crest in 11A. Next chunk "
            "should advance attic / yawn / moon. Do not redo placed crests."
        ),
    }
    if slim:
        return _slim_packet_for_coexist(packet)
    return packet


def _call_via_wh3_ssh(system: str, user: str) -> tuple[dict, dict]:
    payload = {
        "model": "muse-glimmer",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 8192,
        "chat_template_kwargs": {"reasoning_strength": "low"},
    }
    REQ_OUT.write_text(json.dumps(payload), encoding="utf-8")
    subprocess.run(["scp", str(REQ_OUT), f"{WH3}:{WH3_REQ}"], check=True)
    curl = (
        f'curl.exe -s --max-time 900 -H "Content-Type: application/json" '
        f"-d @{WH3_REQ} http://127.0.0.1:8000/v1/chat/completions -o {WH3_RAW}"
    )
    subprocess.run(["ssh", WH3, curl], check=True, timeout=920)
    subprocess.run(["scp", f"{WH3}:{WH3_RAW}", str(RAW_OUT)], check=True)
    raw = json.loads(RAW_OUT.read_text(encoding="utf-8"))
    if "choices" not in raw:
        raise RuntimeError(f"muse http error: {json.dumps(raw)[:800]}")
    msg = raw["choices"][0]["message"]
    content = msg.get("content") or msg.get("reasoning_content") or ""
    return _extract_json(content), raw


def _preview(plan: dict) -> dict:
    chunk = plan.get("next_chunk") or plan.get("next_leg") or {}
    steps = chunk.get("steps") or []
    return {
        "why_tea_unlocked": plan.get("why_tea_unlocked"),
        "why_not_naked_attic": plan.get("why_not_naked_attic"),
        "why_box_118": plan.get("why_box_118"),
        "kit_plan": plan.get("kit_plan"),
        "end_anchor": chunk.get("end_anchor_beat_id"),
        "why": chunk.get("why"),
        "n_steps": len(steps),
        "steps": [
            {
                "n": s.get("n"),
                "op": s.get("op"),
                "edge_id": s.get("edge_id"),
                "pickup_id": s.get("pickup_id"),
                "site_id": s.get("site_id"),
                "beat_id": s.get("beat_id"),
                "room_id": s.get("room_id"),
                "held_on_exit": s.get("held_on_exit"),
            }
            for s in steps
        ],
        "beat_order_head": (plan.get("beat_order") or [])[:20],
    }


def main() -> None:
    slim = "--slim" in sys.argv
    packet = build_pl134_packet(slim=slim)
    system = (
        PASS1_SYSTEM
        + "\n\nFROM PL134 ROOFED PASSAGE AFTER PLACE_WIND_CREST:\n"
        "You are in 11A. wind_crest is PLACED. chunk_final on cp05_shield_key.\n"
        "HP 68 Fine. star+sun+wind placed. shield_key boxed. armor_key held.\n"
        "tea 103->104 unlocked. Do not redo placed crests.\n"
        "YAWN KIT: beretta7+shotgun4 is NOT enough. Must stage bazooka_acid@212,\n"
        "withdraw acid_rounds from box, loot 208/209/20A before attic/Yawn.\n"
        "BOX: from 11A use 118 via 11A->10A->10B->118 ONLY — never detour to 100.\n"
        "Fleet/learner are stopped — this is an authoring call only."
    )
    sections = [
        ("ROLE: system", system),
        ("ROLE: user", USER),
        ("PHASE1_PACKET", compact(packet)),
    ]
    sep = "\n\n" + "=" * 88 + "\n"
    prompt = sep.join(f"{title}\n{'-' * len(title)}\n{body}" for title, body in sections) + "\n"
    PROMPT_OUT.write_text(prompt, encoding="utf-8")
    ALMANAC_OUT.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    print(f"wrote {PROMPT_OUT} chars={len(prompt)} approx_tokens={len(prompt)//4}")
    print(f"wrote {ALMANAC_OUT}")
    print("frontier", [b["id"] for b in packet["open_frontier_beats"]])
    print("remaining", [b["id"] for b in packet["remaining_mandatory_beats"]])
    print("tip_room", packet["tip"]["pose"]["room_id"], "hp", packet["tip"]["pose"]["hp"])
    print("held", [r["item"] for r in packet["tip"]["inventory"] if r.get("item")])
    print("n_edges", len(packet.get("directed_edges") or []))
    if "--build-only" in sys.argv:
        system_m, user_m = _split_prompt(prompt)
        REQ_OUT.write_text(
            json.dumps(
                {
                    "model": "muse-glimmer",
                    "messages": [
                        {"role": "system", "content": system_m},
                        {"role": "user", "content": user_m},
                    ],
                    "max_tokens": 8192,
                    "chat_template_kwargs": {"reasoning_strength": "low"},
                }
            ),
            encoding="utf-8",
        )
        print(f"wrote {REQ_OUT} build-only")
        return

    system_m, user_m = _split_prompt(prompt)
    plan, raw = _call_via_wh3_ssh(system_m, user_m)
    PLAN_OUT.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    usage = (raw or {}).get("usage") or {}
    print(f"wrote {PLAN_OUT} usage={usage}")
    print(json.dumps(_preview(plan), indent=2))
    try:
        errs = validate_pass1_plan(plan, ctx=packet)
        print("validate_ok", not errs)
        if errs:
            print("validate_errs", errs[:12])
    except Exception as e:
        print("validate_skip", type(e).__name__, e)


if __name__ == "__main__":
    main()
