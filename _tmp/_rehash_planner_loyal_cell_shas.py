#!/usr/bin/env python3
"""Rehash planner-loyal meta sidecar/state SHAs after sidecar edits.

Fixes ``[pb] refusing cell State sha mismatch`` when kills regen rewrote
``cell.sidecar.json`` without updating ``meta.json`` hashes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.go_explore_merge import CELL_META_NAME, CELL_SIDECAR_NAME, CELL_STATE_NAME
from re1_rl.planner_loyal_cells import _sha256_file

_RECOMP_STATE = "cell.pst"


def _default_roots() -> list[Path]:
    rl = Path(os.environ.get("RE1_RL_ROOT") or ROOT)
    recomp = Path(
        os.environ.get("RE1_RECOMP_ROOT")
        or ("D:/re1_recomp" if Path("D:/re1_recomp").is_dir() else "C:/re1_recomp")
    )
    return [
        p
        for p in (
            rl / "states" / "planner_loyal",
            recomp / "ecosystem" / "states" / "recomp_pl",
        )
        if (p / "cells").is_dir()
    ]


def rehash_root(root: Path, *, dry_run: bool) -> None:
    wrote = 0
    for slot in sorted((root / "cells").iterdir()):
        if not slot.is_dir() or not slot.name.startswith("pl"):
            continue
        meta_p = slot / CELL_META_NAME
        side_p = slot / CELL_SIDECAR_NAME
        if not meta_p.is_file() or not side_p.is_file():
            continue
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        state_p = slot / _RECOMP_STATE
        if not state_p.is_file():
            state_p = slot / CELL_STATE_NAME
        new_side = _sha256_file(side_p)
        new_state = _sha256_file(state_p) if state_p.is_file() else None
        old_side = str(meta.get("sidecar_sha256") or "")
        old_state = str(meta.get("state_sha256") or "")
        changed = False
        if old_side != new_side:
            meta["sidecar_sha256"] = new_side
            changed = True
        if new_state and old_state != new_state:
            meta["state_sha256"] = new_state
            changed = True
        if not changed:
            continue
        wrote += 1
        if dry_run:
            print(f"{slot.name}: would_rehash side={old_side[:8]}->{new_side[:8]}", flush=True)
            continue
        meta_p.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"REHASH {root} dry_run={int(dry_run)} wrote={wrote}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", action="append", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    roots = list(args.root) if args.root else _default_roots()
    for root in roots:
        rehash_root(root.resolve(), dry_run=bool(args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
