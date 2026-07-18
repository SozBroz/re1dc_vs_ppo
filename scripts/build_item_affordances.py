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
        "notes": "Helmet-key doors across mansion",
    },
    "lockpick": {
        "pickup_rooms": ["106"],
        "use_rooms": ["102", "111", "401"],
        "door_edges": [
            {"from_room": "104", "to_room": "10F", "door_id": "104->10F"},
            {"from_room": "107", "to_room": "108", "door_id": "107->108"},
        ],
        "notes": "Locked desks; Barry gives in tea room",
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
        "notes": "Armor room",
    },
    "sword_key": {
        "pickup_rooms": [],
        "use_rooms": ["20D"],
        "door_edges": [],
        "notes": "Chris-only in standard; kept for data completeness",
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
