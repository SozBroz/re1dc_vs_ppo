#!/usr/bin/env python3
"""Remove failed Go-Explore cell staging debris and orphan directories."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.go_explore_capture import (  # noqa: E402
    cells_root,
    purge_orphan_cell_dirs,
    reconcile_archive_missing_bundles,
    resolve_archive_path,
)
from re1_rl.go_explore_archive import GoExploreArchive  # noqa: E402


def _dir_size_gb(path: Path) -> float:
    if not path.is_dir():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024**3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="RE1 repo root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--also-lua",
        action="store_true",
        help="Also purge legacy lua/data/go_explore/cells under project root",
    )
    parser.add_argument(
        "--nuke-all",
        action="store_true",
        help="Delete entire cells/ trees (archive metadata kept unless --with-archive)",
    )
    parser.add_argument(
        "--with-archive",
        action="store_true",
        help="With --nuke-all, also remove archive.json",
    )
    parser.add_argument(
        "--reconcile-archive",
        action="store_true",
        help="Remove archive.json rows whose cell bundles are missing on disk",
    )
    args = parser.parse_args()
    root = args.project_root.resolve()

    targets: list[Path] = [cells_root(root)]
    if args.also_lua:
        legacy = root / "lua" / "data" / "go_explore" / "cells"
        if legacy not in targets:
            targets.append(legacy)

    for cells in targets:
        if not cells.is_dir():
            print(f"skip (missing): {cells}")
            continue
        before = _dir_size_gb(cells)
        if args.nuke_all:
            shutil.rmtree(cells, ignore_errors=True)
            cells.mkdir(parents=True, exist_ok=True)
            print(f"nuke {cells} (was {before:.2f} GB)")
        else:
            removed = purge_orphan_cell_dirs(cells)
            after = _dir_size_gb(cells)
            print(f"purge {cells}: removed {removed} dirs, {before:.2f} GB -> {after:.2f} GB")

    if args.nuke_all and args.with_archive:
        archive = resolve_archive_path(root)
        if archive.is_file():
            archive.unlink()
            print(f"removed archive {archive}")

    archive_path = resolve_archive_path(root)
    if args.reconcile_archive or args.nuke_all:
        arch = GoExploreArchive(archive_path)
        try:
            arch.load()
        except (OSError, ValueError, json.JSONDecodeError):
            arch.cells = {}
        removed = reconcile_archive_missing_bundles(arch, project_root=root)
        if removed:
            print(f"reconciled archive {archive_path}: removed {removed} stale row(s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
