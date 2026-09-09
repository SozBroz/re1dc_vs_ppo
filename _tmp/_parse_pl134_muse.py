"""Parse latest pl134 Muse raw into response + step dump."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "_tmp"), str(ROOT / "scripts")]

from pl134_muse_next import _preview, build_pl134_packet  # noqa: E402
from probe_muse_phase1 import _extract_json  # noqa: E402
from re1_rl.phase1_route_council import validate_pass1_plan  # noqa: E402

raw = json.loads((ROOT / "_tmp/pl134_muse_next_raw.json").read_text(encoding="utf-8"))
msg = raw["choices"][0]["message"]
content = msg.get("content") or msg.get("reasoning_content") or ""
plan = _extract_json(content)
(ROOT / "_tmp/pl134_muse_next_response.json").write_text(
    json.dumps(plan, indent=2), encoding="utf-8"
)
print(json.dumps(_preview(plan), indent=2))
steps = (plan.get("next_chunk") or {}).get("steps") or []
print("--- ALL STEPS ---")
for s in steps:
    bits = [f"n={s.get('n')}", s.get("op") or ""]
    for k in ("edge_id", "pickup_id", "room_id", "beat_id"):
        if s.get(k):
            bits.append(f"{k}={s[k]}")
    if s.get("held_on_exit"):
        bits.append("held_on_exit=yes")
    if s.get("note"):
        bits.append(f"# {s['note']}")
    print(" | ".join(bits))
edges = [s.get("edge_id") for s in steps if s.get("op") == "traverse"]
box_rooms = [s.get("room_id") for s in steps if s.get("op") in ("use_box", "go_to_box")]
print("box_rooms", box_rooms)
print(
    "used_100_path",
    any(
        e and ("->100" in e or e.startswith("100->") or "109->" in e or "101->100" in e)
        for e in edges
    ),
)
print(
    "used_118_path",
    any(e and ("10B->118" in e or "118->" in e) for e in edges),
)
errs = validate_pass1_plan(plan, ctx=build_pl134_packet())
print("validate_ok", not errs, "n_errs", len(errs))
for e in errs[:15]:
    print(" -", e)
