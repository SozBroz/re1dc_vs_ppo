#!/usr/bin/env python3
"""Backfill planner-loyal meta.json quality to 11 dims on disk.

Lexicon:
  (hp, total_kills, ammo, healing, slots, poison, -ink, -box, -frames,
   stretch_kills, hop_score_milli)

Unknown new dims use -99999. Almanac totals/stretch come from meta.kills or
sidecar planner_loyal.kills when present. hop_score_milli stays -99999 unless
meta.hop_score is set.

Default roots (whichever exist):
  <rl>/states/planner_loyal
  <recomp>/ecosystem/states/recomp_pl

Usage:
  python _tmp/_stitch_planner_loyal_quality_11.py
  python _tmp/_stitch_planner_loyal_quality_11.py --dry-run
  python _tmp/_stitch_planner_loyal_quality_11.py --root D:/re1_rl/states/planner_loyal
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.go_explore_merge import CELL_META_NAME, CELL_SIDECAR_NAME
from re1_rl.planner_loyal_cells import (
    PLANNER_LOYAL_QUALITY_LEN,
    PLANNER_LOYAL_QUALITY_UNKNOWN,
    hop_score_to_milli,
    stitch_planner_loyal_quality,
)


def _default_roots() -> list[Path]:
    rl = Path(os.environ.get("RE1_RL_ROOT") or ROOT)
    recomp = Path(
        os.environ.get("RE1_RECOMP_ROOT")
        or (
            "D:/re1_recomp"
            if Path("D:/re1_recomp").is_dir()
            else "C:/re1_recomp"
        )
    )
    candidates = [
        rl / "states" / "planner_loyal",
        recomp / "ecosystem" / "states" / "recomp_pl",
    ]
    return [p for p in candidates if (p / "cells").is_dir()]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _kills_block(meta: dict, side: dict) -> dict:
    for src in (meta.get("kills"), (side.get("planner_loyal") or {}).get("kills")):
        if isinstance(src, dict) and src:
            return src
    return {}


def _known_int(block: dict, key: str) -> int | None:
    if key not in block:
        return None
    try:
        return max(0, int(block[key]))
    except (TypeError, ValueError):
        return None


def _known_hop(meta: dict) -> float | None:
    if "hop_score" not in meta:
        return None
    raw = meta.get("hop_score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def stitch_cell(slot: Path, *, dry_run: bool) -> str:
    meta_p = slot / CELL_META_NAME
    if not meta_p.is_file():
        return "skip_no_meta"
    try:
        meta = _load_json(meta_p)
    except (OSError, json.JSONDecodeError, TypeError):
        return "skip_bad_meta"
    raw_q = list(meta.get("quality") or [])
    if not raw_q:
        return "skip_empty_quality"

    side: dict = {}
    side_p = slot / CELL_SIDECAR_NAME
    if side_p.is_file():
        try:
            side = _load_json(side_p)
        except (OSError, json.JSONDecodeError, TypeError):
            side = {}

    kills = _kills_block(meta, side)
    total = _known_int(kills, "almanac_total")
    stretch = _known_int(kills, "almanac_stretch")
    hop = _known_hop(meta)
    new_q = stitch_planner_loyal_quality(
        raw_q,
        total_kills=total,
        stretch_kills=stretch,
        hop_score=hop,
    )
    assert len(new_q) == PLANNER_LOYAL_QUALITY_LEN
    if list(raw_q) == new_q:
        return "unchanged"
    if dry_run:
        return f"would_write {raw_q} -> {new_q}"
    meta["quality"] = new_q
    if hop is not None and meta.get("hop_score") is None:
        meta["hop_score"] = hop
    meta_p.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return f"wrote {new_q}"


def stitch_root(root: Path, *, dry_run: bool) -> dict[str, int]:
    cells = root / "cells"
    tallies: dict[str, int] = {
        "wrote": 0,
        "unchanged": 0,
        "skip_no_meta": 0,
        "skip_bad_meta": 0,
        "skip_empty_quality": 0,
        "would_write": 0,
    }
    for slot in sorted(cells.iterdir()):
        if not slot.is_dir() or not slot.name.startswith("pl"):
            continue
        status = stitch_cell(slot, dry_run=dry_run)
        key = status.split(" ", 1)[0]
        tallies[key] = tallies.get(key, 0) + 1
        if status.startswith("wrote") or status.startswith("would_write"):
            print(f"{slot.name}: {status}", flush=True)
    unk = PLANNER_LOYAL_QUALITY_UNKNOWN
    print(
        f"ROOT {root} dry_run={int(dry_run)} "
        f"wrote={tallies.get('wrote', 0)} would={tallies.get('would_write', 0)} "
        f"unchanged={tallies.get('unchanged', 0)} "
        f"skip_empty={tallies.get('skip_empty_quality', 0)} "
        f"unk_sentinel={unk} hop_milli_unk_example={hop_score_to_milli(None)}",
        flush=True,
    )
    return tallies


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        action="append",
        type=Path,
        default=None,
        help="Planner-loyal root (contains cells/). Repeatable.",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    roots = list(args.root) if args.root else _default_roots()
    if not roots:
        print("no planner_loyal roots found", file=sys.stderr)
        return 2
    for root in roots:
        stitch_root(root.resolve(), dry_run=bool(args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
