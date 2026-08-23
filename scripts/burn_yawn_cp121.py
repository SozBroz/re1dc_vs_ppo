"""Burn live yawn_rails cp121 on this machine (cells + manifest + store).

Does not touch Crystals_in_time. Learner store burn must happen first so
workers cannot re-fetch the dead cell.

  venv\\Scripts\\python.exe scripts\\burn_yawn_cp121.py
  venv\\Scripts\\python.exe scripts\\burn_yawn_cp121.py --root C:\\Users\\sshuser\\re1_rl
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = 121


def burn(root: Path, *, index: int = TARGET) -> dict[str, object]:
    yr = root / "states" / "yawn_rails"
    cells_root = yr / "cells"
    manifest_path = yr / "manifest.json"
    store_path = yr / "store.json"
    if not manifest_path.is_file():
        raise SystemExit(f"missing manifest: {manifest_path}")

    removed_dirs: list[str] = []
    cell_dir = cells_root / f"cp{index:02d}"
    if cell_dir.is_dir():
        shutil.rmtree(cell_dir, ignore_errors=True)
        removed_dirs.append(cell_dir.name)

    # Pending proposals for this index (if any).
    proposals = yr / "proposals"
    removed_proposals = 0
    if proposals.is_dir():
        for p in list(proposals.iterdir()):
            name = p.name.lower()
            if f"cp{index:02d}" in name or f"_{index}_" in name or name.endswith(f"_{index}"):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                removed_proposals += 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    before = len(manifest.get("cells") or [])
    kept = [
        c
        for c in (manifest.get("cells") or [])
        if int(c.get("checkpoint_index", -1)) != index
    ]
    av = int(manifest.get("archive_version", 0) or 0) + 1
    manifest["cells"] = kept
    manifest["archive_version"] = av
    manifest["cache_stats"] = {
        "manifest_cells": len(kept),
        "fetched_last_poll": 0,
        "pruned_dirs_last_poll": len(removed_dirs),
        "remote_cell_count": len(kept),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    store_removed = 0
    if store_path.is_file():
        raw = json.loads(store_path.read_text(encoding="utf-8-sig"))
        cells = raw.get("cells") or {}
        for key in list(cells.keys()):
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            if idx == index:
                cells.pop(key, None)
                store_removed += 1
        raw["cells"] = cells
        raw["archive_version"] = av
        store_path.write_text(
            json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    present = sorted(
        int(p.name[2:])
        for p in cells_root.iterdir()
        if cells_root.is_dir()
        and p.is_dir()
        and p.name.startswith("cp")
        and p.name[2:].isdigit()
    )
    out = {
        "root": str(root),
        "index": index,
        "burned_dirs": removed_dirs,
        "removed_proposals": removed_proposals,
        "manifest": f"{before}->{len(kept)}",
        "store_keys_removed": store_removed,
        "archive_version": av,
        "live_tip_max": max(present) if present else None,
        "live_count": len(present),
        "cp121_present": (cells_root / f"cp{index:02d}").is_dir(),
    }
    print(json.dumps(out, indent=2), flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--index", type=int, default=TARGET)
    args = ap.parse_args()
    burn(Path(args.root).resolve(), index=int(args.index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
