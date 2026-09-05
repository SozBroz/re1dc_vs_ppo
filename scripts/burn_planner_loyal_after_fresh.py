"""Drop every planner-loyal cell after pl00 (fresh start).

Keeps pl00. Rewrites store.json + manifest.json and deletes pl01+ dirs so
workers cannot re-fetch the pre-Kenneth-gate opening chain.

Learner RAM still holds the old catalog until the next ingest _load() or a
learner restart. After writing the WH3 store, POST a dummy ingest so the
live process reloads from disk:

  python -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://192.168.0.229:8765/yawn_rails/ingest', data=json.dumps({'proposals':[{'checkpoint_index':999,'quality':[0,0,0,0,0]}]}).encode(), method='POST', headers={'Content-Type':'application/json'}))"

  venv\\Scripts\\python.exe scripts\\burn_planner_loyal_after_fresh.py
  venv\\Scripts\\python.exe scripts\\burn_planner_loyal_after_fresh.py --root C:\\Users\\sshuser\\re1_rl
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEEP = 0


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _pl_index(name: str) -> int | None:
    if not name.startswith("pl"):
        return None
    try:
        return int(name[2:], 10)
    except ValueError:
        return None


def burn(root: Path, *, keep: int = KEEP) -> dict[str, object]:
    pl = root / "states" / "planner_loyal"
    cells_root = pl / "cells"
    staging = pl / ".staging"
    manifest_path = pl / "manifest.json"
    store_path = pl / "store.json"

    removed_dirs: list[str] = []
    if cells_root.is_dir():
        for child in list(cells_root.iterdir()):
            if not child.is_dir():
                continue
            idx = _pl_index(child.name)
            if idx is None or idx <= keep:
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed_dirs.append(child.name)
    if staging.is_dir():
        for child in list(staging.iterdir()):
            idx = _pl_index(child.name)
            if idx is None or idx <= keep:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed_dirs.append(f".staging/{child.name}")

    store_removed = 0
    archive_version = 0
    if store_path.is_file():
        raw = _load_json(store_path)
        cells = raw.get("cells") or {}
        for key in list(cells.keys()):
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            if idx > keep:
                cells.pop(key, None)
                store_removed += 1
        archive_version = int(raw.get("archive_version", 0) or 0) + 1
        raw["cells"] = cells
        raw["archive_version"] = archive_version
        _write_json(store_path, raw)
    elif manifest_path.is_file():
        man = _load_json(manifest_path)
        archive_version = int(man.get("archive_version", 0) or 0) + 1

    kept_manifest = 0
    if manifest_path.is_file() or store_path.is_file():
        if manifest_path.is_file():
            man = _load_json(manifest_path)
        else:
            man = {"schema_version": 1, "cells": []}
        kept = []
        for row in man.get("cells") or []:
            try:
                idx = int(row.get("checkpoint_index", -1))
            except (TypeError, ValueError):
                continue
            if idx <= keep:
                kept.append(row)
        man["cells"] = kept
        if archive_version:
            man["archive_version"] = archive_version
        man["cache_stats"] = {
            "manifest_cells": len(kept),
            "fetched_last_poll": 0,
            "pruned_dirs_last_poll": len(removed_dirs),
            "remote_cell_count": len(kept),
        }
        _write_json(manifest_path, man)
        kept_manifest = len(kept)

    return {
        "root": str(pl),
        "removed_dirs": removed_dirs,
        "store_removed": store_removed,
        "manifest_kept": kept_manifest,
        "archive_version": archive_version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--keep", type=int, default=KEEP)
    args = parser.parse_args()
    if args.keep < 0:
        raise SystemExit("--keep must be >= 0")
    info = burn(args.root.resolve(), keep=args.keep)
    json.dump(info, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
