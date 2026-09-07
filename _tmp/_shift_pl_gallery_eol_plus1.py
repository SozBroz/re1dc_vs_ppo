"""Shift pl48+ one slot for gallery_end_of_life insert. No burn. Leaves pl48 empty."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.planner_loyal import load_chunk  # noqa: E402
from re1_rl.planner_loyal_cells import (  # noqa: E402
    CELL_META_NAME,
    CELL_SIDECAR_NAME,
    planner_loyal_root,
    rewrite_manifest,
    slot_index_for_completed_step,
)

DELTA = 1
SHIFT_FROM = 48
STAMP_NAME = ".gallery_eol_plus1"


def _cell_dir(cells: Path, n: int) -> Path:
    return cells / f"pl{n:02d}"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _patch_indices(cell: Path, new_slot: int, steps: list) -> None:
    side_p = cell / CELL_SIDECAR_NAME
    meta_p = cell / CELL_META_NAME
    if not side_p.is_file():
        raise SystemExit(f"{cell.name}: missing {CELL_SIDECAR_NAME}")
    side = _load_json(side_p)
    pl = dict(side.get("planner_loyal") or {})
    completed = pl.get("completed_step_index")
    if completed is None:
        raise SystemExit(f"{cell.name}: missing completed_step_index")
    new_completed = int(completed) + DELTA
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

    stamp = planner_loyal_root(root) / STAMP_NAME
    steps = list(load_chunk().get("steps") or [])
    if not any(s.get("site_id") == "gallery_end_of_life" for s in steps):
        raise SystemExit("chunk missing gallery_end_of_life; insert that first")

    existing = sorted(
        int(p.name[2:])
        for p in cells.iterdir()
        if p.is_dir() and p.name.startswith("pl") and p.name[2:].isdigit()
    )
    if stamp.is_file() and 48 not in existing:
        print(f"already shifted ({stamp.name} present, no pl48)")
        man = rewrite_manifest(root)
        print(f"manifest cells={len(man.get('cells') or [])}")
        print("SHIFT_OK")
        return 0
    shift = [n for n in existing if n >= SHIFT_FROM]
    print(f"root={root}")
    print(f"shift={shift} -> {[n + DELTA for n in shift]}")

    shift_set = set(shift)
    for n in shift:
        dest = n + DELTA
        if dest in existing and dest not in shift_set:
            raise SystemExit(
                f"collision: pl{n:02d} -> pl{dest:02d} exists and is not shifting"
            )

    if args.dry_run:
        print("dry-run only")
        return 0

    hole = _cell_dir(cells, SHIFT_FROM)
    if hole.exists() and SHIFT_FROM in shift:
        pass
    elif hole.exists() and SHIFT_FROM not in existing:
        raise SystemExit(f"unexpected hole state: {hole}")

    for n in sorted(shift, reverse=True):
        src = _cell_dir(cells, n)
        dst_n = n + DELTA
        dst = _cell_dir(cells, dst_n)
        if dst.exists():
            raise SystemExit(f"dest exists before rename: {dst}")
        print(f"RENAME {src.name} -> {dst.name} (completed +{DELTA})")
        _patch_indices(src, dst_n, steps)
        src.rename(dst)

    left = {
        int(p.name[2:])
        for p in cells.iterdir()
        if p.is_dir() and p.name.startswith("pl") and p.name[2:].isdigit()
    }
    if SHIFT_FROM in left:
        raise SystemExit(f"pl{SHIFT_FROM:02d} should be empty after shift")
    for n in shift:
        if (n + DELTA) not in left:
            raise SystemExit(f"missing shifted pl{n + DELTA:02d}")

    man = rewrite_manifest(root)
    stamp.write_text("gallery_end_of_life plus1\n", encoding="utf-8")
    print(f"manifest cells={len(man.get('cells') or [])}")
    print("SHIFT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
