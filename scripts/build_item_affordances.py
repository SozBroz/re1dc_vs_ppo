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
        "rooms": ["10F", "105"],
        "notes": "Wooden emblem: bar piano chain; gold swap at 10F alcove",
    },
    "gold_emblem": {
        "rooms": ["105", "10F"],
        "notes": "Dining fireplace reveals shield_key",
    },
    "shield_key": {
        "rooms": ["10D", "117", "105", "10C"],
        "notes": "Helmet-key doors across mansion",
    },
    "lockpick": {
        "rooms": ["102", "111", "401", "104"],
        "notes": "Locked desks; Barry gives in tea room",
    },
    "armor_key": {
        "rooms": ["205"],
        "notes": "Armor room",
    },
    "sword_key": {
        "rooms": ["20D"],
        "notes": "Chris-only in standard; kept for data completeness",
    },
}


def main() -> int:
    with ROOM_ITEMS.open(encoding="utf-8") as f:
        raw = json.load(f)

    affordances: dict[str, dict] = defaultdict(
        lambda: {"rooms": set(), "door_edges": [], "notes": ""}
    )

    for room_id, block in raw.items():
        if not isinstance(block, dict):
            continue
        for row in block.get("items", []):
            name = canonical_item(str(row.get("name", "")))
            if not name:
                continue
            if row.get("key_item"):
                affordances[name]["rooms"].add(str(room_id))
                note = str(row.get("notes", "") or "").strip()
                if note and not affordances[name]["notes"]:
                    affordances[name]["notes"] = note
            gate = row.get("gate") or {}
            for req in gate.get("requires", []) or []:
                req_name = canonical_item(str(req))
                if req_name:
                    affordances[req_name]["rooms"].add(str(room_id))
                    gnote = str(gate.get("notes", "") or "").strip()
                    if gnote:
                        affordances[req_name]["notes"] = gnote

    for name, extra in _MANUAL.items():
        affordances[name]["rooms"].update(extra.get("rooms", []))
        if extra.get("notes"):
            affordances[name]["notes"] = extra["notes"]

    out: dict[str, dict] = {}
    for name in sorted(affordances):
        rooms = sorted(affordances[name]["rooms"])
        if not rooms:
            continue
        out[name] = {
            "rooms": rooms,
            "door_edges": list(affordances[name]["door_edges"]),
            "notes": affordances[name]["notes"],
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[build_item_affordances] wrote {len(out)} items -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
