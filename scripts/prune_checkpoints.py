"""Keep only the N newest numbered checkpoints per directory.

Updates ``latest.json`` in each pruned directory to point at the highest
step count remaining.

Usage:
    python scripts/prune_checkpoints.py --keep 5
    python scripts/prune_checkpoints.py --keep 5 --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.checkpoint_io import (  # noqa: E402
    find_latest_checkpoint,
    is_valid_checkpoint,
    write_latest_pointer,
)

_STEP_RE = re.compile(r"_(\d+)_steps\.zip$", re.I)


def _steps_from_name(path: Path) -> int:
    m = _STEP_RE.search(path.name)
    return int(m.group(1)) if m else -1


def prune_dir(ckpt_dir: Path, *, keep: int, dry_run: bool) -> tuple[int, int]:
    zips = [
        p for p in ckpt_dir.glob("ppo_re1_*_steps.zip")
        if is_valid_checkpoint(p)
    ]
    if len(zips) <= keep:
        return 0, len(zips)
    zips.sort(key=lambda p: (_steps_from_name(p), p.stat().st_mtime), reverse=True)
    survivors = zips[:keep]
    victims = zips[keep:]
    for p in victims:
        if dry_run:
            print(f"[dry-run] delete {p}")
        else:
            p.unlink()
            print(f"[prune] deleted {p.name}")
    if not dry_run and survivors:
        best = max(survivors, key=_steps_from_name)
        write_latest_pointer(ckpt_dir, best, steps=_steps_from_name(best))
        print(f"[prune] {ckpt_dir.name}/latest.json -> {best.name}")
    return len(victims), len(survivors)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=5)
    ap.add_argument("--root", default=str(PROJECT_ROOT / "data" / "checkpoints"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"[prune] missing {root}")
        return 1

    total_del = 0
    for ckpt_dir in sorted({root, *root.rglob("*")}):
        if not ckpt_dir.is_dir():
            continue
        if not any(ckpt_dir.glob("ppo_re1_*_steps.zip")):
            continue
        n_del, n_keep = prune_dir(ckpt_dir, keep=args.keep, dry_run=args.dry_run)
        if n_del:
            print(f"[prune] {ckpt_dir}: kept {n_keep}, removed {n_del}")
        total_del += n_del

    # Stale atomic-write temps in data/
    data_dir = PROJECT_ROOT / "data"
    for tmp in data_dir.glob("_ckpt_write*"):
        if args.dry_run:
            print(f"[dry-run] delete temp {tmp.name}")
        else:
            tmp.unlink(missing_ok=True)

    # Top-level latest.json when a single active run dir exists
    active = find_latest_checkpoint(root / "reward_tune_1040k") or find_latest_checkpoint(root)
    if active and not args.dry_run:
        top_latest = root / "latest.json"
        write_latest_pointer(root, active, steps=_steps_from_name(active))
        print(f"[prune] data/checkpoints/latest.json -> {active.name}")

    print(f"[prune] done: removed {total_del} checkpoint(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
