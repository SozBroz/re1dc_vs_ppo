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
    promote_cell_files,
    slot_content_shas,
    slot_matches_content,
    yawn_cells_locked,
    yawn_rails_root,
    yawn_rails_sync_enabled,
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
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
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


def ensure_yawn_bundle_cached(
    client: _YawnRailsClient,
    checkpoint_index: int,
    project_root: Path | str,
    *,
    expected_state_sha256: str | None = None,
    expected_sidecar_sha256: str | None = None,
) -> Path | None:
    """Eager-fetch ``cpNN`` into ``states/yawn_rails/cells/``. Returns cell dir.

    Cache hit requires on-disk ``cell.State`` bytes to match ``expected_state_sha256``.
    Matching ``meta.json`` tokens alone is not a hit.
    """
    root = yawn_rails_root(project_root)
    idx = int(checkpoint_index)
    dest = cell_slot_dir(root, idx)
    if slot_matches_content(
        dest,
        state_sha256=expected_state_sha256,
        sidecar_sha256=expected_sidecar_sha256,
    ):
        return dest

    cell_id = cell_dir_name(idx)
    try:
        blob = client.fetch_yawn_rails_bundle(cell_id)
    except Exception:
        return None
    if not blob:
        return None

    incoming = dest.parent / f".incoming_{cell_id}_{os.getpid()}"
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(BytesIO(blob)) as zf:
            zf.extractall(incoming)
        got = slot_content_shas(incoming)
        if got is None:
            shutil.rmtree(incoming, ignore_errors=True)
            return None
        got_state, got_side = got
        want_state = str(expected_state_sha256 or "").strip()
        if want_state and got_state != want_state:
            shutil.rmtree(incoming, ignore_errors=True)
            return None
        want_side = str(expected_sidecar_sha256 or "").strip()
        if want_side and got_side != want_side:
            shutil.rmtree(incoming, ignore_errors=True)
            return None
        meta_p = incoming / CELL_META_NAME
        meta: dict[str, Any] = {}
        if meta_p.is_file():
            try:
                loaded = json.loads(meta_p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                meta = loaded
        meta["checkpoint_index"] = idx
        meta["state_sha256"] = got_state
        meta["sidecar_sha256"] = got_side
        meta_p.write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        with yawn_cells_locked(root, holder="yawn_poll_install"):
            promote_cell_files(incoming, dest)
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        return None
    shutil.rmtree(incoming, ignore_errors=True)
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


def _local_content_drift(project_root: Path | str, local: dict[str, Any]) -> bool:
    """True when a catalog row's files no longer match its advertised hashes."""
    root = yawn_rails_root(project_root)
    for row in local.get("cells") or []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row["checkpoint_index"])
        except (KeyError, TypeError, ValueError):
            continue
        want_state = str(row.get("state_sha256") or "").strip() or None
        if not want_state:
            continue
        slot = cell_slot_dir(root, idx)
        if not slot_matches_content(
            slot,
            state_sha256=want_state,
            sidecar_sha256=str(row.get("sidecar_sha256") or "") or None,
        ):
            return True
    return False


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
        prev_by_idx: dict[int, dict[str, Any]] = {}
        for r in local.get("cells") or []:
            if not isinstance(r, dict):
                continue
            try:
                prev_by_idx[int(r["checkpoint_index"])] = r
            except (KeyError, TypeError, ValueError):
                continue
        rows: list[dict[str, Any]] = []
        valid: set[int] = set()
        for row in remote_cells:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row["checkpoint_index"])
            except (KeyError, TypeError, ValueError):
                continue
            want_state = str(row.get("state_sha256") or "") or None
            want_side = str(row.get("sidecar_sha256") or "") or None
            slot = cell_slot_dir(yawn_rails_root(project_root), idx)
            was_hit = slot_matches_content(
                slot, state_sha256=want_state, sidecar_sha256=want_side
            )
            cached = ensure_yawn_bundle_cached(
                client,
                idx,
                project_root,
                expected_state_sha256=want_state,
                expected_sidecar_sha256=want_side,
            )
            if cached is None:
                # Keep prior row only if local files still match that row's hashes.
                prev = prev_by_idx.get(idx)
                if prev is not None:
                    prev_state = str(prev.get("state_sha256") or "") or None
                    if slot_matches_content(
                        slot,
                        state_sha256=prev_state,
                        sidecar_sha256=str(prev.get("sidecar_sha256") or "") or None,
                    ):
                        rows.append(dict(prev))
                        valid.add(idx)
                continue
            if not was_hit:
                fetched += 1
            valid.add(idx)
            out_row = {
                "checkpoint_index": idx,
                "checkpoint_id": row.get("checkpoint_id", ""),
                "room_id": row.get("room_id", ""),
                "quality": list(row.get("quality") or []),
                "bundle_sha256": row.get("bundle_sha256", ""),
                "state_sha256": row.get("state_sha256", ""),
                "sidecar_sha256": row.get("sidecar_sha256", ""),
                "bytes": int(row.get("bytes") or 0),
                "state_path": (
                    row.get("state_path")
                    or f"{DEFAULT_YAWN_RAILS_REL}/cells/{cell_dir_name(idx)}/{CELL_STATE_NAME}"
                ),
                "sidecar_path": (
                    row.get("sidecar_path")
                    or f"{DEFAULT_YAWN_RAILS_REL}/cells/{cell_dir_name(idx)}/{CELL_SIDECAR_NAME}"
                ),
                **{
                    key: row[key]
                    for key in (
                        "inventory_free_slots",
                        "next_checkpoint_id",
                        "next_slots_needed",
                        "inventory_feasible",
                        "captured_in_box_room",
                    )
                    if key in row
                },
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
        if int(since_version) != 0 and _local_content_drift(project_root, local):
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
    if not yawn_rails_sync_enabled():
        return float(last_poll_mono)
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
