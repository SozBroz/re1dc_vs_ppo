"""Compact a Go-Explore archive to the semantic pose cap.

For each ``(room_id, milestone_digest)`` bucket over the pose cap, keep the
best N cells by eviction score and remove the rest (JSON + bundle dirs).

Usage:
    python scripts/go_explore_compact_archive.py --archive data/go_explore/archive.json --dry-run
    python scripts/go_explore_compact_archive.py --archive data/go_explore/archive.json --execute
    python scripts/go_explore_compact_archive.py --archive data/go_explore/archive.json --report
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.go_explore_archive import archive_locked  # noqa: E402
from re1_rl.go_explore_merge import GoExploreMerge, cells_dir  # noqa: E402
from re1_rl.go_explore_semantic import keep_best_rows, pose_cap  # noqa: E402


def _plan_evictions(
    archive,
    *,
    keep_n: int,
) -> list[tuple[tuple[str, str], list[str]]]:
    """Return ``[(bucket, [record_ids_to_remove...]), ...]``."""
    plan: list[tuple[tuple[str, str], list[str]]] = []
    for bucket, cells in archive.cells_by_semantic_bucket().items():
        if len(cells) <= keep_n:
            continue
        keep = keep_best_rows(cells, keep_n)
        keep_ids = {c.record_id for c in keep}
        drop_ids = sorted(c.record_id for c in cells if c.record_id not in keep_ids)
        if drop_ids:
            plan.append((bucket, drop_ids))
    return plan


def compact_archive(
    archive_path: Path,
    *,
    execute: bool,
    keep_n: int | None = None,
) -> dict:
    path = Path(archive_path).resolve()
    n = int(keep_n if keep_n is not None else pose_cap())
    merge = GoExploreMerge(path)
    plan = _plan_evictions(merge.archive, keep_n=n)
    drop_total = sum(len(ids) for _, ids in plan)
    report = {
        "archive": str(path),
        "pose_cap": n,
        "cells_before": len(merge.archive.cells),
        "buckets_over_cap": len(plan),
        "cells_to_remove": drop_total,
        "dry_run": not execute,
        "buckets": [
            {
                "room_id": b[0],
                "milestone_digest": b[1],
                "remove_record_ids": ids,
            }
            for b, ids in plan
        ],
    }
    if not execute:
        report["cells_after"] = report["cells_before"] - drop_total
        return report
    if drop_total == 0:
        report["cells_after"] = report["cells_before"]
        return report

    with archive_locked(path, holder="go_explore_compact"):
        merge._load()
        plan = _plan_evictions(merge.archive, keep_n=n)
        root_cells = cells_dir(path)
        for bucket, ids in plan:
            for rid in ids:
                merge.archive.remove_cell(rid)
                cell_dir = root_cells / rid
                if cell_dir.is_dir():
                    shutil.rmtree(cell_dir, ignore_errors=True)
                print(
                    f"go_explore compact removed {rid} "
                    f"bucket={bucket[0]}/{bucket[1]}",
                    flush=True,
                )
        merge.archive_version += 1
        merge._persist_unlocked()

    report["cells_after"] = len(merge.archive.cells)
    report["archive_version"] = merge.archive_version
    report["dry_run"] = False
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Compact Go-Explore archive to pose cap")
    ap.add_argument(
        "--archive",
        type=Path,
        default=PROJECT_ROOT / "data" / "go_explore" / "archive.json",
        help="Path to archive.json",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned removals without writing (default)",
    )
    mode.add_argument(
        "--report",
        action="store_true",
        help="Alias for --dry-run",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Apply compaction (delete excess cells + bump archive_version)",
    )
    ap.add_argument(
        "--pose-cap",
        type=int,
        default=None,
        help=f"Keep this many poses per semantic bucket (default: env/pose_cap={pose_cap()})",
    )
    args = ap.parse_args()
    execute = bool(args.execute)
    report = compact_archive(args.archive, execute=execute, keep_n=args.pose_cap)

    print("Go-Explore archive compaction")
    print(f"  archive:          {report['archive']}")
    print(f"  pose_cap:         {report['pose_cap']}")
    print(f"  cells_before:     {report['cells_before']}")
    print(f"  buckets_over_cap: {report['buckets_over_cap']}")
    print(f"  cells_to_remove:  {report['cells_to_remove']}")
    print(f"  cells_after:      {report['cells_after']}")
    print(f"  mode:             {'execute' if execute else 'dry-run'}")
    for bucket in report["buckets"]:
        digest = bucket["milestone_digest"]
        if len(digest) > 48:
            digest = digest[:45] + "..."
        print(
            f"  drop {len(bucket['remove_record_ids'])} from "
            f"{bucket['room_id']} | {digest}"
        )
        for rid in bucket["remove_record_ids"][:8]:
            print(f"    - {rid}")
        extra = len(bucket["remove_record_ids"]) - 8
        if extra > 0:
            print(f"    … +{extra} more")
    if execute and report.get("archive_version") is not None:
        print(f"  archive_version:  {report['archive_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
