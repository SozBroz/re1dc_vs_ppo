"""Burn poisoned greenhouse/102 cells; shift later cells +2 for RGRG insert.

KEEP pl00-pl61 (through pump).
DELETE pl62-pl69 (pl62 unmappable green-before-armor; pl63-69 herb vacuum / 102 stuck).
RENAME plN -> pl(N+2) for N >= 70 (high to low), bump completed/planner indices +2.
Rewrite manifest.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.planner_loyal import load_chunk  # noqa: E402
from re1_rl.planner_loyal_cells import (  # noqa: E402
    CELL_META_NAME,
    CELL_SIDECAR_NAME,
    CELL_STATE_NAME,
    planner_loyal_root,
    rewrite_manifest,
    slot_index_for_completed_step,
)

BURN_LO = 62
BURN_HI = 69
SHIFT_FROM = 70


def _cell_dir(cells: Path, n: int) -> Path:
    return cells / f"pl{n:02d}"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _patch_indices(cell: Path, new_slot: int, delta: int, steps: list) -> None:
    side_p = cell / CELL_SIDECAR_NAME
    meta_p = cell / CELL_META_NAME
    side = _load_json(side_p)
    pl = dict(side.get("planner_loyal") or {})
    completed = pl.get("completed_step_index")
    if completed is None:
        raise SystemExit(f"{cell.name}: missing completed_step_index")
    new_completed = int(completed) + int(delta)
    expect_slot = slot_index_for_completed_step(new_completed, steps)
    if expect_slot != new_slot:
        raise SystemExit(
            f"{cell.name}: slot math {expect_slot} != rename target {new_slot} "
            f"(completed {completed}->{new_completed})"
        )
    pl["completed_step_index"] = new_completed
    pl["slot_index"] = new_slot
    side["planner_loyal"] = pl
    _write_json(side_p, side)

    if meta_p.is_file():
        meta = _load_json(meta_p)
        meta["checkpoint_index"] = new_slot
        meta["planner_step_index"] = new_completed
        # Keep human id stable-ish but reflect new step number when patterned.
        cid = str(meta.get("checkpoint_id") or "")
        if cid.startswith("cp05_shield_key_step"):
            meta["checkpoint_id"] = f"cp05_shield_key_step{new_completed + 1:02d}"
        _write_json(meta_p, meta)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()
    cells = planner_loyal_root(root) / "cells"
    if not cells.is_dir():
        raise SystemExit(f"missing cells dir: {cells}")

    steps = list(load_chunk().get("steps") or [])
    existing = sorted(
        int(p.name[2:])
        for p in cells.iterdir()
        if p.is_dir() and p.name.startswith("pl") and p.name[2:].isdigit()
    )
    burn = [n for n in existing if BURN_LO <= n <= BURN_HI]
    shift = [n for n in existing if n >= SHIFT_FROM]
    print(f"root={root}")
    print(f"burn={burn}")
    print(f"shift={shift} -> {[n + 2 for n in shift]}")

    # Collision check: targets must be free or themselves in the shift set.
    shift_set = set(shift)
    for n in shift:
        dest = n + 2
        if dest in existing and dest not in shift_set:
            raise SystemExit(f"collision: pl{n:02d} -> pl{dest:02d} exists and is not shifting")

    if args.dry_run:
        print("dry-run only")
        return 0

    for n in burn:
        d = _cell_dir(cells, n)
        print(f"DELETE {d.name}")
        shutil.rmtree(d)

    for n in sorted(shift, reverse=True):
        src = _cell_dir(cells, n)
        dst_n = n + 2
        dst = _cell_dir(cells, dst_n)
        if dst.exists():
            raise SystemExit(f"dest exists before rename: {dst}")
        print(f"RENAME {src.name} -> {dst.name} (completed +2)")
        _patch_indices(src, dst_n, delta=2, steps=steps)
        src.rename(dst)

    man = rewrite_manifest(root)
    print(f"manifest cells={len(man.get('cells') or [])}")
    # Sanity: no burned dirs, shifted dirs present
    left = {int(p.name[2:]) for p in cells.iterdir() if p.is_dir() and p.name.startswith("pl")}
    bad = [n for n in left if BURN_LO <= n <= BURN_HI]
    if bad:
        raise SystemExit(f"burn leftovers: {bad}")
    for n in shift:
        if (n + 2) not in left:
            raise SystemExit(f"missing shifted pl{n + 2:02d}")
        if n in left and n not in {x + 2 for x in shift}:
            # old number only ok if another cell shifted into it
            pass
    print("SHIFT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
