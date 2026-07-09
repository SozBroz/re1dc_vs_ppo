"""Merge empirical pickup logs into data/item_positions.json.

Sources, in trust order:
  1. data/pickups_empirical.json  -- ground truth from log_door_transitions.py
     (pose where the item entered inventory), confidence "high"
  2. MANUAL_ANCHORS below          -- hand-measured / landmark-derived poses,
     confidence as annotated (never overwrite an empirical entry)
  3. data/rdt_item_positions.json  -- from scripts/merge_rdt_into_data.py
     (RDT ITEM_SET zones matched to room_items names), confidence "medium"

Output schema ("<room>:<item>" keys, consumed by spatial_encoder.ItemPositions):
  {"105:emblem": {"x": 30700, "z": 7200, "source": "empirical",
                  "confidence": "high", "notes": "..."}}

Re-run after every logging session or RDT merge; idempotent merge, empirical wins.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.item_todo import canonical_item

PICKUPS_PATH = PROJECT_ROOT / "data" / "pickups_empirical.json"
RDT_POSITIONS_PATH = PROJECT_ROOT / "data" / "rdt_item_positions.json"
OUT_PATH = PROJECT_ROOT / "data" / "item_positions.json"

# Mirror log_door_transitions.STACK_PICKUP_ALIAS for rows missing ground_item.
STACK_PICKUP_ALIAS: dict[str, str] = {"beretta": "clip"}

# (room, item, x, z, confidence, notes) -- anchor-based guesses from
# doors_empirical.json entry poses + Evil Resource prose. Keep sparse;
# empirical logging is the real pipeline.
MANUAL_ANCHORS: list[tuple[str, str, int, int, str, str]] = [
    ("105", "emblem", 30700, 7200, "medium",
     "dining table west end, near main-hall double door (= Jill spawn pose)"),
]


def main() -> None:
    existing: dict = {}
    if OUT_PATH.is_file():
        with OUT_PATH.open(encoding="utf-8") as f:
            existing = json.load(f)

    out: dict = {k: v for k, v in existing.items() if not k.startswith("_")}

    for room, item, x, z, conf, notes in MANUAL_ANCHORS:
        key = f"{room}:{canonical_item(item)}"
        if out.get(key, {}).get("source") == "empirical":
            continue
        out[key] = {"x": x, "z": z, "source": "manual",
                    "confidence": conf, "notes": notes}

    n_empirical = 0
    n_rdt = 0
    if PICKUPS_PATH.is_file():
        with PICKUPS_PATH.open(encoding="utf-8") as f:
            pickups = json.load(f)
        for p in pickups:
            kind = p.get("kind", "new_item")
            note = (f"pose at {kind} (log_door_transitions.py)"
                    + (f", +{p['qty_delta']}" if kind == "ammo_stack" else ""))
            names = [canonical_item(p["item"])]
            if p.get("ground_item"):
                names.append(canonical_item(p["ground_item"]))
            elif kind == "ammo_stack" and names[0] in STACK_PICKUP_ALIAS:
                names.append(STACK_PICKUP_ALIAS[names[0]])
            for name in dict.fromkeys(names):
                key = f"{p['room']}:{name}"
                out[key] = {"x": int(p["x"]), "z": int(p["z"]),
                            "source": "empirical", "confidence": "high",
                            "notes": note}
                n_empirical += 1
    else:
        print(f"[items] {PICKUPS_PATH} missing -- run a logging session first")

    if RDT_POSITIONS_PATH.is_file():
        with RDT_POSITIONS_PATH.open(encoding="utf-8") as f:
            rdt_pos = json.load(f)
        for key, entry in rdt_pos.items():
            if key.startswith("_"):
                continue
            if canonical_item(key.split(":", 1)[-1]).startswith("@slot_"):
                continue  # skip raw slot keys in merged output
            cur = out.get(key, {})
            if cur.get("source") == "empirical":
                continue
            if cur.get("source") == "manual" and cur.get("confidence") == "high":
                continue
            out[key] = {
                "x": int(entry["x"]),
                "z": int(entry["z"]),
                "source": "rdt",
                "confidence": entry.get("confidence", "medium"),
                "notes": entry.get("notes", ""),
            }
            n_rdt += 1
    else:
        print(f"[items] {RDT_POSITIONS_PATH} missing — run merge_rdt_into_data.py")

    result = {"_meta": {
        "source": "pickups_empirical.json + manual anchors + rdt_item_positions.json",
        "schema": "'<room>:<item>' -> {x, z, source, confidence, notes}",
        "generated_by": "scripts/build_item_positions.py",
    }}
    result.update(dict(sorted(out.items())))
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"[items] wrote {OUT_PATH}: {len(out)} positions "
          f"({n_empirical} empirical, {n_rdt} rdt rows merged)")


if __name__ == "__main__":
    main()
