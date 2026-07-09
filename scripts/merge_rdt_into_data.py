#!/usr/bin/env python3
"""Merge RDT parse output into RL data files for the NN observation stack.

Reads data/rdt_extracted.json and updates:
  - data/item_positions.json      (via build_item_positions merge tier)
  - data/doors_rdt.json           (RDT door graph; empirical wins in RoomGraph)
  - data/room_enemies.json        (add x,z spawn coords when absent)
  - data/rdt_interactables.json    (typewriters, boxes, triggers — future obs)

Run order:
  1. scripts/extract_rdt_from_disc.py
  2. scripts/parse_rdt_scd.py
  3. scripts/merge_rdt_into_data.py
  4. scripts/build_item_positions.py   (re-merge tiers)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.item_todo import canonical_item

RDT_EXTRACTED = ROOT / "data" / "rdt_extracted.json"
ROOM_ITEMS = ROOT / "data" / "room_items.json"
ROOM_ENEMIES = ROOT / "data" / "room_enemies.json"
DOORS_RDT = ROOT / "data" / "doors_rdt.json"
INTERACTABLES_OUT = ROOT / "data" / "rdt_interactables.json"
RDT_POSITIONS = ROOT / "data" / "rdt_item_positions.json"

# RE1 enemy model id -> room_enemies.json enemy_type (partial; extend as needed)
MODEL_TO_TYPE: dict[int, str] = {
    1: "zombie",
    2: "zombie",
    3: "zombie_dog",
    4: "zombie_dog",
    17: "zombie",
    18: "zombie",
}


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _match_items(room_id: str, rdt_items: list[dict], room_item_rows: list[dict]) -> list[tuple[str, dict]]:
    """Pair room_items inventory rows with RDT zones sorted by slot_id."""
    targets = [
        it for it in room_item_rows
        if it.get("in_inventory_table") and it.get("name")
    ]
    cands = sorted(
        [it for it in rdt_items if it.get("type") in ("pickable", "object")],
        key=lambda r: int(r.get("slot_id", 0)),
    )
    pairs: list[tuple[str, dict]] = []
    for tgt, c in zip(targets, cands):
        pairs.append((canonical_item(tgt["name"]), c))
    return pairs


def build_rdt_item_positions(rdt: dict, room_items: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for room_id, block in rdt.items():
        if room_id.startswith("_") or not isinstance(block, dict):
            continue
        ri = room_items.get(room_id, {})
        rows = ri.get("items", []) if isinstance(ri, dict) else []
        for name, rdt_it in _match_items(room_id, block.get("items", []), rows):
            key = f"{room_id}:{name}"
            out[key] = {
                "x": int(rdt_it["x"]),
                "z": int(rdt_it["z"]),
                "source": "rdt",
                "confidence": "medium",
                "notes": f"slot_{rdt_it['slot_id']} type={rdt_it.get('type')} ({rdt_it.get('script', '')})",
                "rdt_slot": int(rdt_it["slot_id"]),
            }
        # also expose raw slots for debugging
        for rdt_it in block.get("items", []):
            slot = int(rdt_it["slot_id"])
            key = f"{room_id}:@slot_{slot}"
            if key in out:
                continue
            out[key] = {
                "x": int(rdt_it["x"]),
                "z": int(rdt_it["z"]),
                "source": "rdt_slot",
                "confidence": "low",
                "notes": f"unmapped slot {slot} type={rdt_it.get('type')}",
                "rdt_slot": slot,
            }
    return out


def build_rdt_doors(rdt: dict) -> dict:
    doors: dict = {
        "_meta": {
            "source": "rdt_extracted.json DOOR_SET opcodes",
            "generated_by": "scripts/merge_rdt_into_data.py",
        }
    }
    for room_id, block in rdt.items():
        if room_id.startswith("_") or not isinstance(block, dict):
            continue
        for d in block.get("doors", []):
            dest = str(d["dest_room"])
            if dest == "00" or len(dest) < 2:
                continue
            key = f"{room_id}->{dest}"
            doors[key] = {
                "from_room": room_id,
                "to_room": dest,
                "door_x": int(d["zone_x"]),
                "door_z": int(d["zone_z"]),
                "entry_x": int(d.get("entry_x") or 0),
                "entry_z": int(d.get("entry_z") or 0),
                "entry_facing": int(d.get("entry_dir") or 0),
                "source": "rdt",
                "gated": bool(d.get("gated")),
                "notes": f"rdt door_id={d.get('door_id')} ({d.get('script', '')})",
            }
    return doors


def merge_enemy_positions(rdt: dict, room_enemies: dict, rooms: dict) -> tuple[dict, int]:
    n = 0
    for room_id, block in rdt.items():
        if room_id.startswith("_") or not isinstance(block, dict):
            continue
        if room_id not in rooms:
            continue
        if room_id not in room_enemies:
            continue
        spawns = block.get("enemies", [])
        if not spawns:
            continue
        room = room_enemies[room_id]
        if not room.get("enemies"):
            continue
        for i, row in enumerate(room["enemies"]):
            if "x" in row and "z" in row:
                continue
            if i < len(spawns):
                sp = spawns[i]
                if int(sp["model"]) == 0:
                    continue
                row["x"] = int(sp["x"])
                row["z"] = int(sp["z"])
                row["model_id"] = int(sp["model"])
                row["notes"] = (row.get("notes", "") + f" | RDT @({sp['x']},{sp['z']})").strip()
                n += 1
    return room_enemies, n


def build_interactables(rdt: dict) -> dict:
    out: dict = {"_meta": {"source": "rdt ITEM_SET interactables"}}
    for room_id, block in rdt.items():
        if room_id.startswith("_") or not isinstance(block, dict):
            continue
        ints = block.get("interactables", [])
        if ints:
            out[room_id] = ints
    return out


def main() -> None:
    if not RDT_EXTRACTED.is_file():
        print(f"[merge] missing {RDT_EXTRACTED} — run parse_rdt_scd.py first")
        sys.exit(1)

    rdt = _load_json(RDT_EXTRACTED)
    room_items = _load_json(ROOM_ITEMS)
    room_enemies = _load_json(ROOM_ENEMIES)
    rooms = _load_json(ROOT / "data" / "rooms.json")

    positions = build_rdt_item_positions(rdt, room_items)
    RDT_POSITIONS.write_text(json.dumps(positions, indent=2), encoding="utf-8")

    doors = build_rdt_doors(rdt)
    DOORS_RDT.write_text(json.dumps(doors, indent=2), encoding="utf-8")

    room_enemies, n_enemy = merge_enemy_positions(rdt, room_enemies, rooms)
    meta = room_enemies.get("_meta", {})
    if isinstance(meta, dict):
        meta["rdt_positions_merged"] = n_enemy
        room_enemies["_meta"] = meta
    ROOM_ENEMIES.write_text(json.dumps(room_enemies, indent=2), encoding="utf-8")

    interactables = build_interactables(rdt)
    INTERACTABLES_OUT.write_text(json.dumps(interactables, indent=2), encoding="utf-8")

    named = sum(1 for k in positions if not k.split(":", 1)[-1].startswith("@slot_"))
    print(f"[merge] rdt_item_positions: {len(positions)} keys ({named} named)")
    print(f"[merge] doors_rdt: {len(doors) - 1} edges")
    print(f"[merge] room_enemies: {n_enemy} spawn coords attached")
    print(f"[merge] interactables: {len(interactables) - 1} rooms")
    print("[merge] next: python scripts/build_item_positions.py")


if __name__ == "__main__":
    main()
