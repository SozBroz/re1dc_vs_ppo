#!/usr/bin/env python3
"""Parse all extracted RDT files -> data/rdt_extracted.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.rdt_parser import parse_room_rdt, room_rdt_to_dict

RDT_DIR = ROOT / "data" / "rdt_raw"
OUT = ROOT / "data" / "rdt_extracted.json"
MANIFEST = ROOT / "data" / "rdt_manifest.json"


def main() -> None:
    if not RDT_DIR.is_dir():
        print(f"[rdt] missing {RDT_DIR} — run scripts/extract_rdt_from_disc.py first")
        sys.exit(1)

    # Prefer variant 0; fall back to variant 1 if 0 missing
    by_room: dict[str, Path] = {}
    for p in sorted(RDT_DIR.glob("ROOM*.RDT")):
        key = p.name[4:7].upper()  # hex room id e.g. 105
        variant = int(p.name[7])
        prev = by_room.get(key)
        if prev is None or (variant == 0 and int(prev.name[7]) != 0):
            by_room[key] = p

    extracted: dict[str, dict] = {}
    manifest: dict[str, dict] = {}
    stats = {"doors": 0, "items": 0, "enemies": 0, "interactables": 0}

    for room_id in sorted(by_room.keys(), key=lambda x: (int(x[0], 16), int(x[1:], 16))):
        path = by_room[room_id]
        room = parse_room_rdt(path)
        if room is None:
            continue
        d = room_rdt_to_dict(room)
        extracted[room_id] = d
        manifest[room_id] = {
            "file": path.name,
            "stage": room.stage,
            "variant": room.variant,
        }
        stats["doors"] += len(room.doors)
        stats["items"] += len(room.items)
        stats["enemies"] += len(room.enemies)
        stats["interactables"] += len(room.interactables)

    payload = {
        "_meta": {
            "source": "PS1 SLUS-00551 RDT SCD parse",
            "generated_by": "scripts/parse_rdt_scd.py",
            "rooms": len(extracted),
            **stats,
        },
        **extracted,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[rdt] wrote {OUT}: {len(extracted)} rooms, "
          f"{stats['items']} pickables, {stats['doors']} doors, "
          f"{stats['enemies']} enemies, {stats['interactables']} interactables")


if __name__ == "__main__":
    main()
