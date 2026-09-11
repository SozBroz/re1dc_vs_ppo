#!/usr/bin/env python3
"""March planner-loyal capture units pl01 → end and report tape status.

Walks ``data/planner_loyal_room_segments.json`` in pl order; any plNN in
``[pl_first_expected, max_slot]`` not covered by a segment is a solo unit
(gallery pl41–pl49 and armor pl82–pl85 are solos by design). For each unit the
exit cell is checked for: minted state, ``leg_replay.json``, ``leg_policy.npz``,
and best ``S_room`` (segments only).

This is the programmatic march: it lists exactly which room/solo tapes still
need capturing, in play order, so capture runs can work the list front to back.

Usage:
  venv\\Scripts\\python.exe scripts/march_planner_tapes.py
  venv\\Scripts\\python.exe scripts/march_planner_tapes.py --from 22 --to 49
  venv\\Scripts\\python.exe scripts/march_planner_tapes.py --json-out _tmp/march.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _state_file(cell_dir: Path) -> Path | None:
    for name in ("cell.pst", "cell.State"):
        p = cell_dir / name
        if p.is_file():
            return p
    return None


def build_units(from_pl: int, to_pl: int) -> list[dict]:
    from re1_rl.planner_room_segments import clear_room_segments_cache, load_room_segments

    clear_room_segments_cache()
    data = load_room_segments(ROOT, force=True)
    segs = sorted(
        [s for s in (data.get("segments") or []) if isinstance(s, dict)],
        key=lambda s: int(s.get("pl_first", 0)),
    )
    covered: set[int] = set()
    for s in segs:
        covered.update(range(int(s["pl_first"]), int(s["pl_last"]) + 1))
    units: list[dict] = []
    for s in segs:
        lo, hi = int(s["pl_first"]), int(s["pl_last"])
        if hi < from_pl or lo > to_pl:
            continue
        units.append({
            "kind": "segment",
            "unit": str(s.get("id")),
            "pl_first": lo,
            "pl_last": hi,
            "exit_slot": hi,
        })
    for slot in range(from_pl, to_pl + 1):
        if slot in covered:
            continue
        units.append({
            "kind": "solo",
            "unit": f"pl{slot:02d}_solo",
            "pl_first": slot,
            "pl_last": slot,
            "exit_slot": slot,
        })
    units.sort(key=lambda u: (u["pl_first"], u["pl_last"]))
    return units


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="from_pl", type=int, default=1)
    ap.add_argument("--to", dest="to_pl", type=int, default=200)
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--allow-gaps", action="store_true",
                    help="exit 0 even when cells/tapes are missing")
    args = ap.parse_args()

    from re1_rl.planner_loyal_cells import cell_slot_dir, planner_loyal_root

    best: dict = {}
    best_path = ROOT / "data" / "planner_loyal_room_segment_best.json"
    if best_path.is_file():
        try:
            best = (json.loads(best_path.read_text(encoding="utf-8")) or {}).get("best") or {}
        except (OSError, json.JSONDecodeError):
            best = {}

    cells_root = planner_loyal_root(ROOT)
    units = build_units(int(args.from_pl), int(args.to_pl))
    rows: list[dict] = []
    for u in units:
        cell_dir = cell_slot_dir(cells_root, int(u["exit_slot"]))
        has_state = _state_file(cell_dir) is not None
        has_tape = (cell_dir / "leg_replay.json").is_file()
        has_policy = (cell_dir / "leg_policy.npz").is_file()
        s_room = None
        if u["kind"] == "segment":
            rec = best.get(u["unit"]) or {}
            s_room = rec.get("S_room")
        rows.append({**u, "state": has_state, "tape": has_tape,
                     "policy": has_policy, "S_room": s_room})

    print(f"{'unit':28s} {'pls':11s} state tape policy S_room", flush=True)
    missing: list[str] = []
    for r in rows:
        rng = f"{r['pl_first']:03d}-{r['pl_last']:03d}"
        s = f"{r['S_room']}" if r["S_room"] is not None else "-"
        print(
            f"{r['unit']:28s} {rng:11s} "
            f"{'Y' if r['state'] else '.'}     "
            f"{'Y' if r['tape'] else '.'}    "
            f"{'Y' if r['policy'] else '.'}      {s}",
            flush=True,
        )
        if not (r["state"] and r["tape"]):
            missing.append(r["unit"])
    print(
        f"[march] units={len(rows)} complete={len(rows) - len(missing)} "
        f"missing={len(missing)}",
        flush=True,
    )
    if missing:
        print(f"[march] TODO: {' '.join(missing)}", flush=True)
    if args.json_out is not None:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"rows": rows, "missing": missing}, indent=2) + "\n",
                       encoding="utf-8")
        print(f"[march] wrote {out}", flush=True)
    if missing and not args.allow_gaps:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
