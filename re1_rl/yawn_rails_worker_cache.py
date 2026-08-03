"""Worker-side Yawn rails manifest poll + eager bundle mirror."""

from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from re1_rl.go_explore_merge import CELL_META_NAME, CELL_SIDECAR_NAME, CELL_STATE_NAME
from re1_rl.yawn_rails_sync import (
    DEFAULT_YAWN_RAILS_REL,
    MANIFEST_FILENAME,
    cell_dir_name,
    cell_slot_dir,
    yawn_rails_root,
)

DEFAULT_MANIFEST_POLL_S = 60.0


class _YawnRailsClient(Protocol):
    def fetch_yawn_rails_manifest(self, since_version: int = 0) -> dict[str, Any]: ...

    def fetch_yawn_rails_bundle(self, cell_id: str) -> bytes: ...


def yawn_rails_manifest_poll_s(default: float = DEFAULT_MANIFEST_POLL_S) -> float:
    raw = os.environ.get("RE1_YAWN_RAILS_MANIFEST_POLL_S", "").strip()
    if not raw:
        # Share Go-Explore poll cadence when unset.
        raw = os.environ.get("RE1_GO_EXPLORE_MANIFEST_POLL_S", "").strip()
    if not raw:
        return float(default)
    try:
        return max(1.0, float(raw))
    except ValueError:
        return float(default)


def load_local_yawn_manifest(project_root: Path | str) -> dict[str, Any]:
    root = yawn_rails_root(project_root)
    path = root / MANIFEST_FILENAME
    if not path.is_file():
        return {"schema_version": 1, "archive_version": 0, "route_id": None, "cells": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "archive_version": 0, "route_id": None, "cells": []}
    if not isinstance(raw, dict):
        return {"schema_version": 1, "archive_version": 0, "route_id": None, "cells": []}
    return {
        "schema_version": int(raw.get("schema_version", 1) or 1),
        "archive_version": int(raw.get("archive_version", 0) or 0),
        "route_id": raw.get("route_id"),
        "cells": list(raw.get("cells") or []),
        "cache_stats": dict(raw.get("cache_stats") or {}),
    }


def save_local_yawn_manifest(project_root: Path | str, manifest: dict[str, Any]) -> None:
    root = yawn_rails_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_FILENAME
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _local_meta_sha(slot: Path) -> str | None:
    meta_p = slot / CELL_META_NAME
    if not meta_p.is_file():
        return None
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sha = meta.get("bundle_sha256")
    return str(sha) if sha else None


def ensure_yawn_bundle_cached(
    client: _YawnRailsClient,
    checkpoint_index: int,
    project_root: Path | str,
    *,
    expected_sha256: str | None = None,
) -> Path | None:
    """Eager-fetch ``cpNN`` into ``states/yawn_rails/cells/``. Returns cell dir."""
    root = yawn_rails_root(project_root)
    idx = int(checkpoint_index)
    dest = cell_slot_dir(root, idx)
    state_p = dest / CELL_STATE_NAME
    side_p = dest / CELL_SIDECAR_NAME
    if state_p.is_file() and side_p.is_file():
        if expected_sha256:
            local_sha = _local_meta_sha(dest)
            if local_sha is None or local_sha == str(expected_sha256):
                return dest
        else:
            return dest

    cell_id = cell_dir_name(idx)
    try:
        blob = client.fetch_yawn_rails_bundle(cell_id)
    except Exception:
        return None
    if not blob:
        return None

    incoming = dest.parent / f".incoming_{cell_id}"
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(BytesIO(blob)) as zf:
            zf.extractall(incoming)
        if not (incoming / CELL_STATE_NAME).is_file():
            shutil.rmtree(incoming, ignore_errors=True)
            return None
        if not (incoming / CELL_SIDECAR_NAME).is_file():
            shutil.rmtree(incoming, ignore_errors=True)
            return None
        meta_p = incoming / CELL_META_NAME
        if not meta_p.is_file():
            import hashlib

            meta = {
                "checkpoint_index": idx,
                "bundle_sha256": (
                    str(expected_sha256)
                    if expected_sha256
                    else hashlib.sha256(blob).hexdigest()
                ),
                "bytes": len(blob),
            }
            meta_p.write_text(
                json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(str(incoming), str(dest))
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        return None
    return dest


def prune_stale_yawn_cells(
    project_root: Path | str, valid_indices: set[int]
) -> int:
    root = yawn_rails_root(project_root) / "cells"
    if not root.is_dir():
        return 0
    removed = 0
    for p in root.iterdir():
        if not p.is_dir() or p.name.startswith("."):
            continue
        name = p.name
        if not name.startswith("cp"):
            continue
        try:
            idx = int(name[2:], 10)
        except ValueError:
            continue
        if idx not in valid_indices:
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    return removed


def poll_yawn_rails_manifest(
    client: _YawnRailsClient,
    project_root: Path | str,
    *,
    since_version: int,
) -> dict[str, Any]:
    """Fetch learner manifest and eagerly mirror every changed cell."""
    remote = client.fetch_yawn_rails_manifest(since_version=int(since_version))
    remote_ver = int(remote.get("archive_version", 0) or 0)
    remote_cells = list(remote.get("cells") or [])
    remote_cell_count = remote.get("cell_count")
    local = load_local_yawn_manifest(project_root)
    pruned = 0
    fetched = 0

    if remote_cells:
        rows: list[dict[str, Any]] = []
        valid: set[int] = set()
        for row in remote_cells:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row["checkpoint_index"])
            except (KeyError, TypeError, ValueError):
                continue
            valid.add(idx)
            sha = str(row.get("bundle_sha256") or "") or None
            if ensure_yawn_bundle_cached(
                client, idx, project_root, expected_sha256=sha
            ) is not None:
                fetched += 1
            out_row = {
                "checkpoint_index": idx,
                "checkpoint_id": row.get("checkpoint_id", ""),
                "room_id": row.get("room_id", ""),
                "quality": list(row.get("quality") or []),
                "bundle_sha256": row.get("bundle_sha256", ""),
                "bytes": int(row.get("bytes") or 0),
                "state_path": (
                    row.get("state_path")
                    or f"{DEFAULT_YAWN_RAILS_REL}/cells/{cell_dir_name(idx)}/{CELL_STATE_NAME}"
                ),
                "sidecar_path": (
                    row.get("sidecar_path")
                    or f"{DEFAULT_YAWN_RAILS_REL}/cells/{cell_dir_name(idx)}/{CELL_SIDECAR_NAME}"
                ),
            }
            rows.append(out_row)
        pruned = prune_stale_yawn_cells(project_root, valid)
        local = {
            "schema_version": 1,
            "archive_version": remote_ver,
            "route_id": remote.get("route_id"),
            "cells": sorted(rows, key=lambda r: int(r["checkpoint_index"])),
        }
    elif remote_cell_count is not None:
        local_count = len(local.get("cells") or [])
        if int(remote_cell_count) != local_count and int(since_version) != 0:
            return poll_yawn_rails_manifest(
                client, project_root, since_version=0
            )
        local["archive_version"] = remote_ver
    else:
        local["archive_version"] = remote_ver

    local["cache_stats"] = {
        "manifest_cells": len(local.get("cells") or []),
        "fetched_last_poll": fetched,
        "pruned_dirs_last_poll": pruned,
        "remote_cell_count": (
            int(remote_cell_count) if remote_cell_count is not None else None
        ),
    }
    save_local_yawn_manifest(project_root, local)
    return local


def maybe_poll_yawn_rails_manifest(
    client: _YawnRailsClient,
    project_root: Path | str,
    *,
    last_poll_mono: float,
    poll_s: float | None = None,
) -> float:
    """Poll learner yawn_rails manifest when interval elapsed."""
    interval = (
        yawn_rails_manifest_poll_s() if poll_s is None else float(poll_s)
    )
    now = time.monotonic()
    if now - float(last_poll_mono) < interval:
        return float(last_poll_mono)
    local = load_local_yawn_manifest(project_root)
    since = int(local.get("archive_version", 0) or 0)
    poll_yawn_rails_manifest(client, project_root, since_version=since)
    return now
