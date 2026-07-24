#!/usr/bin/env python3
"""Build data/item_affordances.json from room_items gates + key items."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.item_todo import canonical_item

ROOM_ITEMS = ROOT / "data" / "room_items.json"
OUT = ROOT / "data" / "item_affordances.json"

# Manual supplements when gate.requires is incomplete (north star examples).
_MANUAL: dict[str, dict] = {
    "emblem": {
        "pickup_rooms": ["10F"],
        "use_rooms": ["105"],
        "door_edges": [],
        "notes": "Wooden emblem: bar piano chain; gold swap at 10F alcove",
    },
    "gold_emblem": {
        "pickup_rooms": ["10F"],
        "use_rooms": ["105"],
        "door_edges": [],
        "notes": "Dining fireplace reveals shield_key",
    },
    "shield_key": {
        "pickup_rooms": ["105"],
        "use_rooms": ["20E"],
        "door_edges": [
            {"from_room": "20E", "to_room": "210", "door_id": "20E->210"},
        ],
        "notes": "ER: opens Attic Entry→Attic only. Pickup behind dining clock after gold_emblem.",
    },
    "lockpick": {
        "pickup_rooms": ["106"],
        "use_rooms": ["102", "111", "216", "401", "406", "40A", "10E"],
        "door_edges": [
            {"from_room": "103", "to_room": "10E", "door_id": "103->10E"},
            {"from_room": "104", "to_room": "10F", "door_id": "104->10F"},
            {"from_room": "107", "to_room": "108", "door_id": "107->108"},
        ],
        "notes": "Jill: Barry in tea room; locked desks/lockers; Keeper's Bedroom door off Central Corridor",
    },
    "armor_key": {
        "pickup_rooms": ["10C"],
        "use_rooms": [],
        "door_edges": [
            {"from_room": "109", "to_room": "114", "door_id": "109->114"},
            {"from_room": "101", "to_room": "102", "door_id": "101->102"},
            {"from_room": "204", "to_room": "205", "door_id": "204->205"},
            {"from_room": "204", "to_room": "20D", "door_id": "204->20D"},
            {"from_room": "207", "to_room": "208", "door_id": "207->208"},
            {"from_room": "106", "to_room": "111", "door_id": "106->111"},
        ],
        "notes": (
            "Unlocks: Winding Passage→Outside Boiler (109→114); "
            "East Stairway 1F→Vacant Room (101→102); "
            "'C' Passage→Armor Room (204→205); "
            "'C' Passage→Pillar Passage (204→20D); "
            "West Stairway 2F→Deer Room (207→208); "
            "Main Hall 1F→Dressing Room (106→111)"
        ),
    },
    "chemical": {
        "pickup_rooms": ["11B"],
        "use_rooms": ["10C"],
        "door_edges": [],
        "notes": "Herbicide (wiki name; RAM id chemical). Pour into greenhouse pump to kill vines on crest",
    },
    "wind_crest": {
        "pickup_rooms": ["10D"],
        "use_rooms": ["11A"],
        "door_edges": [],
        "notes": "Place on Roofed Passage courtyard door puzzle (11A)",
    },
    "star_crest": {
        "pickup_rooms": ["117"],
        "use_rooms": ["11A"],
        "door_edges": [],
        "notes": "Place on Roofed Passage courtyard door puzzle (11A)",
    },
    "sun_crest": {
        "pickup_rooms": ["205"],
        "use_rooms": ["11A"],
        "door_edges": [],
        "notes": "Place on Roofed Passage courtyard door puzzle (11A)",
    },
    "moon_crest": {
        "pickup_rooms": ["210"],
        "use_rooms": ["11A"],
        "door_edges": [],
        "notes": "Place on Roofed Passage courtyard door puzzle (11A)",
    },
    "blue_jewel": {
        "pickup_rooms": ["105", "202"],
        "use_rooms": ["10D"],
        "door_edges": [],
        "notes": "Pickup 202 (push statue) and 105 (landing). Insert in tiger eye at 10D",
    },
    "square_crank": {
        "pickup_rooms": ["11B"],
        "use_rooms": ["301"],
        "door_edges": [],
        "notes": "Crest shed / store room shelf; use at Water Gate (301)",
    },
    "mo_disc": {
        "pickup_rooms": ["217", "503", "30B"],
        "use_rooms": ["509", "507", "510"],
        "door_edges": [],
        "notes": "Pickups Library B (217), lab stairs (503), boulder passage (30B). Use MO readers 509/507/510",
    },
    "battery": {
        "pickup_rooms": ["219", "500"],
        "use_rooms": ["302", "500"],
        "door_edges": [],
        "notes": "Closet 219 + lab entry 500. Use at Falls elevator (302) and lab entry socket (500)",
    },
    "flare": {
        "pickup_rooms": ["303"],
        "use_rooms": ["303"],
        "door_edges": [],
        "notes": "Barry gives flare; pickup and use on Heliport (303) to signal Brad",
    },
    "doom_book_1": {
        "pickup_rooms": ["119"],
        "use_rooms": ["119"],
        "door_edges": [],
        "notes": "Courtyard Study bookshelf. Examine pages (directions + Cross) for eagle medal",
    },
    "doom_book_2": {
        "pickup_rooms": ["303"],
        "use_rooms": ["303"],
        "door_edges": [],
        "notes": "Heliport desk. Examine pages (directions + Cross) for wolf medal",
    },
    "wolf_medal": {
        "pickup_rooms": ["303"],
        "use_rooms": ["305"],
        "door_edges": [],
        "notes": "Inside Doom Book 2 (303). Place both medals at Fountain (305) for lab entry",
    },
    "eagle_medal": {
        "pickup_rooms": ["119"],
        "use_rooms": ["305"],
        "door_edges": [],
        "notes": "Inside Doom Book 1 (119). Place both medals at Fountain (305) for lab entry",
    },
    "hex_crank": {
        "pickup_rooms": ["30A"],
        "use_rooms": ["306", "310", "30B"],
        "door_edges": [],
        "notes": "Enrico room pickup. Use in Item Passage (306), Underground Entry (310), Boulder Passage (30B)",
    },
    "slides": {
        "pickup_rooms": ["506"],
        "use_rooms": ["504"],
        "door_edges": [],
        "notes": "Small Lab (506) pickup; Visual Data / Conference Room projector (504)",
    },
    "power_room_key": {
        "pickup_rooms": ["504"],
        "use_rooms": ["50C"],
        "door_edges": [],
        "notes": "Conference Room shelf (504); unlocks power maze at Front of Elevator (50C)",
    },
    "lab_key_2": {
        "pickup_rooms": ["513"],
        "use_rooms": ["500"],
        "door_edges": [],
        "notes": "Laboratory master key on Tyrant Room desk (513); use at Laboratory Entry (500)",
    },
    "control_room_key": {
        "pickup_rooms": ["402"],
        "use_rooms": ["40E"],
        "door_edges": [
            {"from_room": "40E", "to_room": "411", "door_id": "40E->411"},
        ],
        "notes": "Room 001 bathroom drain; opens Water Tank→Control Room (40E→411)",
    },
    "dorm_key_002": {
        "pickup_rooms": ["408"],
        "use_rooms": ["406"],
        "door_edges": [
            {"from_room": "406", "to_room": "407", "door_id": "406->407"},
        ],
        "notes": "Beehive Passage table; opens Room 002→002 Bathroom (406→407)",
    },
    "helmet_key": {
        "pickup_rooms": ["40C"],
        "use_rooms": [],
        "door_edges": [
            {"from_room": "10A", "to_room": "119", "door_id": "10A->119"},
            {"from_room": "201", "to_room": "215", "door_id": "201->215"},
            {"from_room": "20B", "to_room": "20C", "door_id": "20B->20C"},
        ],
        "notes": (
            "Unlocks: Back Passage→Courtyard Study (10A→119); "
            "East Stairway 2F→Trophy Room (201→215); "
            "Lesson Room Entry→Lesson Room (20B→20C)"
        ),
    },
    "dorm_key_003": {
        "pickup_rooms": ["410"],
        "use_rooms": ["408"],
        "door_edges": [
            {"from_room": "408", "to_room": "40A", "door_id": "408->40A"},
        ],
        "notes": "Arms storehouse shelf; opens Beehive Passage→Room 003 (408→40A)",
    },
}


def _empty_entry() -> dict:
    return {
        "pickup_rooms": set(),
        "use_rooms": set(),
        "door_edges": [],
        "notes": "",
    }


def main() -> int:
    with ROOM_ITEMS.open(encoding="utf-8") as f:
        raw = json.load(f)

    affordances: dict[str, dict] = defaultdict(_empty_entry)

    for room_id, block in raw.items():
        if not isinstance(block, dict):
            continue
        for row in block.get("items", []):
            name = canonical_item(str(row.get("name", "")))
            if not name:
                continue
            if row.get("key_item"):
                affordances[name]["pickup_rooms"].add(str(room_id))
                note = str(row.get("notes", "") or "").strip()
                if note and not affordances[name]["notes"]:
                    affordances[name]["notes"] = note
            gate = row.get("gate") or {}
            for req in gate.get("requires", []) or []:
                req_name = canonical_item(str(req))
                if req_name:
                    affordances[req_name]["use_rooms"].add(str(room_id))
                    gnote = str(gate.get("notes", "") or "").strip()
                    if gnote:
                        affordances[req_name]["notes"] = gnote

    for name, extra in _MANUAL.items():
        affordances[name]["pickup_rooms"].update(extra.get("pickup_rooms", []))
        affordances[name]["use_rooms"].update(extra.get("use_rooms", []))
        if extra.get("door_edges"):
            affordances[name]["door_edges"] = list(extra["door_edges"])
        if extra.get("notes"):
            affordances[name]["notes"] = extra["notes"]

    out: dict[str, dict] = {}
    for name in sorted(affordances):
        entry = affordances[name]
        pickup_rooms = sorted(entry["pickup_rooms"])
        use_rooms = sorted(entry["use_rooms"])
        door_edges = list(entry["door_edges"])
        if not pickup_rooms and not use_rooms and not door_edges:
            continue
        rooms = sorted(set(pickup_rooms + use_rooms + [e["to_room"] for e in door_edges]))
        out[name] = {
            "pickup_rooms": pickup_rooms,
            "use_rooms": use_rooms,
            "door_edges": door_edges,
            "rooms": rooms,
            "notes": entry["notes"],
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[build_item_affordances] wrote {len(out)} items -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
