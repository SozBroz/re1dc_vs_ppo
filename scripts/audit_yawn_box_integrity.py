"""Audit yawn curated cells (+ optional QuickSave) for 48-slot box integrity.

Fails hard when any cell has:
  - key items in the box (incl. chemical)
  - any occupied slot past the modeled 16 (NN-invisible)
  - disallowed weapons / non-bank items in modeled slots

Usage:
  python scripts/audit_yawn_box_integrity.py
  python scripts/audit_yawn_box_integrity.py --cells-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.item_box import (  # noqa: E402
    BOX_SLOTS,
    BOX_SLOTS_LIVE,
    box_pollution_reason,
)
from re1_rl.memory_map import ITEM_IDS  # noqa: E402


def _pairs_from_cache(raw: object) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    if not isinstance(raw, list):
        return pairs
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            try:
                pairs.append((int(entry[0]), int(entry[1])))
            except (TypeError, ValueError):
                continue
    return pairs


def _fmt_occ(pairs: list[tuple[int, int]]) -> str:
    parts = []
    for i, (iid, q) in enumerate(pairs):
        if int(iid) == 0:
            continue
        name = ITEM_IDS.get(int(iid), f"0x{int(iid):02x}")
        parts.append(f"{i}:{name}x{q}")
    return "[" + ", ".join(parts) + "]"


def audit_sidecar(path: Path) -> tuple[bool, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = _pairs_from_cache(data.get("box_cache"))
    if not pairs:
        return True, "no_box_cache"
    # Pad to live length so deep checks run even on short caches.
    while len(pairs) < BOX_SLOTS_LIVE:
        pairs.append((0, 0))
    pol = box_pollution_reason(pairs)
    if pol:
        return False, f"{pol} occupied={_fmt_occ(pairs)}"
    # Soft note: modeled free slots for operators.
    free = sum(1 for iid, _ in pairs[:BOX_SLOTS] if int(iid) == 0)
    return True, f"ok free16={free} occupied={_fmt_occ(pairs[:BOX_SLOTS])}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        type=Path,
        default=ROOT / "states" / "yawn_rails" / "cells",
    )
    ap.add_argument(
        "--cells-only",
        action="store_true",
        help="Skip printing per-cell ok lines (errors still print).",
    )
    args = ap.parse_args()

    cells = sorted(args.root.glob("cp*/cell.sidecar.json"))
    if not cells:
        print(f"NO_CELLS under {args.root}", file=sys.stderr)
        return 2

    bad: list[str] = []
    ok_n = 0
    for side in cells:
        cell = side.parent.name
        good, detail = audit_sidecar(side)
        if good:
            ok_n += 1
            if not args.cells_only:
                print(f"OK  {cell}  {detail}")
        else:
            bad.append(f"BAD {cell}  {detail}")
            print(f"BAD {cell}  {detail}", file=sys.stderr)

    print(f"SUMMARY clean={ok_n} polluted={len(bad)} total={len(cells)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
