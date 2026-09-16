"""Durable plNN → mint_policy.policy_version map for march weight restores.

Cells stamp ``meta.mint_policy`` at mint time, but burns delete those dirs.
This map survives burns so a pin on ``plN`` (hunting ``plN+1``) can reload
``data/mint_policies/weights/v<V>.pt`` when that archive still exists.

Schema (``data/planner_mint_policy_map.json``)::

    {
      "schema_version": 1,
      "updated_unix": ...,
      "slots": {
        "23": {"policy_version": 117, "machine": "...", "minted_at_unix": ...}
      }
    }
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

MAP_REL = Path("data/planner_mint_policy_map.json")
SCHEMA_VERSION = 1


def _root(project_root: Path | str | None) -> Path:
    if project_root is not None:
        return Path(project_root)
    env = (os.environ.get("RE1_RL_ROOT") or "").strip()
    return Path(env) if env else Path.cwd()


def map_path(project_root: Path | str | None = None) -> Path:
    return _root(project_root) / MAP_REL


def load_map(project_root: Path | str | None = None) -> dict[str, Any]:
    path = map_path(project_root)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "updated_unix": 0.0, "slots": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "updated_unix": 0.0, "slots": {}}
    if not isinstance(raw, dict):
        return {"schema_version": SCHEMA_VERSION, "updated_unix": 0.0, "slots": {}}
    slots = raw.get("slots")
    if not isinstance(slots, dict):
        slots = {}
    return {
        "schema_version": int(raw.get("schema_version") or SCHEMA_VERSION),
        "updated_unix": float(raw.get("updated_unix") or 0.0),
        "slots": slots,
    }


def save_map(doc: dict[str, Any], project_root: Path | str | None = None) -> Path:
    path = map_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_unix": time.time(),
        "slots": dict(doc.get("slots") or {}),
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _slot_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        ver = int(raw.get("policy_version") or 0)
    except (TypeError, ValueError):
        return None
    if ver <= 0:
        return None
    out: dict[str, Any] = {"policy_version": ver}
    for key in ("machine", "minted_at_unix", "inference_temperature", "mod_drop"):
        if key in raw and raw[key] is not None:
            out[key] = raw[key]
    return out


def note_mint(
    slot: int,
    mint_policy: dict[str, Any] | None,
    *,
    project_root: Path | str | None = None,
) -> int | None:
    """Upsert one slot from a live mint. Returns stored policy_version or None."""
    entry = _slot_entry(mint_policy)
    if entry is None:
        return None
    doc = load_map(project_root)
    slots = dict(doc.get("slots") or {})
    slots[str(int(slot))] = entry
    doc["slots"] = slots
    save_map(doc, project_root)
    return int(entry["policy_version"])


def snapshot_from_cells(project_root: Path | str | None = None) -> dict[str, Any]:
    """Merge every live cell's ``mint_policy`` into the durable map."""
    from re1_rl.planner_loyal_cells import planner_loyal_root

    root = planner_loyal_root(_root(project_root))
    cells = root / "cells"
    doc = load_map(project_root)
    slots = dict(doc.get("slots") or {})
    if cells.is_dir():
        for d in cells.glob("pl*"):
            if not d.is_dir() or not d.name[2:].isdigit():
                continue
            meta_p = d / "meta.json"
            if not meta_p.is_file():
                continue
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            entry = _slot_entry(meta.get("mint_policy"))
            if entry is None:
                continue
            slots[str(int(d.name[2:]))] = entry
    doc["slots"] = slots
    save_map(doc, project_root)
    return doc


def resolve_hunt_mint_version(
    pin_idx: int,
    *,
    project_root: Path | str | None = None,
    require_archive: bool = True,
) -> int | None:
    """Weights for hunting ``pl(pin+1)`` — map entry if archive file exists.

    ``require_archive=True`` (default) only returns a version when
    ``data/mint_policies/weights/v<V>.pt`` is present so march falls back to
    base weights instead of wedging on a missing restore.
    """
    target = int(pin_idx) + 1
    if target < 0:
        return None
    doc = load_map(project_root)
    raw = (doc.get("slots") or {}).get(str(target))
    entry = _slot_entry(raw)
    if entry is None:
        # Prefer live cell meta when the map was never snapshotted.
        try:
            from re1_rl.planner_loyal_cells import cell_dir_name, planner_loyal_root

            meta_p = (
                planner_loyal_root(_root(project_root))
                / "cells"
                / cell_dir_name(target)
                / "meta.json"
            )
            if meta_p.is_file():
                meta = json.loads(meta_p.read_text(encoding="utf-8-sig"))
                entry = _slot_entry(meta.get("mint_policy"))
        except (OSError, ValueError, json.JSONDecodeError, ImportError, TypeError):
            entry = None
    if entry is None:
        return None
    ver = int(entry["policy_version"])
    if not require_archive:
        return ver
    try:
        from re1_rl.distributed.weight_archive import find_weight_file
    except ImportError:
        return None
    if find_weight_file(_root(project_root), ver) is None:
        return None
    return ver
