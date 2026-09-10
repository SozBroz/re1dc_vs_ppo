"""Zero hop_score_milli (quality[10]) on every planner-loyal cell meta.

Remints then rewrite scores under the live hop formula. Leaves other quality
dims untouched. Also clears meta.hop_score when present.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CELLS = ROOT / "states" / "planner_loyal" / "cells"
HOP_DIM = 10


def reset_dir(cells_root: Path, *, dry_run: bool) -> tuple[int, int]:
    touched = 0
    skipped = 0
    for meta_path in sorted(cells_root.glob("pl*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        q = meta.get("quality")
        changed = False
        if isinstance(q, list) and len(q) > HOP_DIM:
            if int(q[HOP_DIM]) != 0:
                q[HOP_DIM] = 0
                changed = True
        if "hop_score" in meta and meta.get("hop_score") not in (None, 0, 0.0):
            meta["hop_score"] = 0.0
            changed = True
        if not changed:
            skipped += 1
            continue
        touched += 1
        if not dry_run:
            meta_path.write_text(
                json.dumps(meta, indent=2) + "\n", encoding="utf-8"
            )
    return touched, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cells-root",
        type=Path,
        default=DEFAULT_CELLS,
        help="planner_loyal cells directory",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = args.cells_root
    if not root.is_dir():
        print(f"MISSING {root}")
        return 2
    touched, skipped = reset_dir(root, dry_run=bool(args.dry_run))
    mode = "DRY" if args.dry_run else "WROTE"
    print(f"{mode} root={root} touched={touched} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
