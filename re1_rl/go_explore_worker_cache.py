"""Worker-side Go-Explore manifest poll + lazy local bundle cache."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from re1_rl.go_explore_merge import (
    CELL_META_NAME,
    CELL_SIDECAR_NAME,
    CELL_STATE_NAME,
    cells_dir,
    go_explore_root,
)

DEFAULT_MANIFEST_POLL_S = 60.0
LOCAL_MANIFEST_NAME = "local_manifest.json"


class _ManifestClient(Protocol):
    def fetch_go_explore_manifest(self, since_version: int = 0) -> dict[str, Any]: ...

    def fetch_go_explore_bundle(self, record_id: str) -> bytes: ...


def manifest_poll_s(default: float = DEFAULT_MANIFEST_POLL_S) -> float:
    raw = os.environ.get("RE1_GO_EXPLORE_MANIFEST_POLL_S", "").strip()
    if not raw:
        return float(default)
    try:
        return max(1.0, float(raw))
    except ValueError:
        return float(default)


def local_manifest_path(local_root: Path | str) -> Path:
    return go_explore_root(local_root) / LOCAL_MANIFEST_NAME


def load_local_manifest(local_root: Path | str) -> dict[str, Any]:
    path = local_manifest_path(local_root)
    if not path.is_file():
        return {"archive_version": 0, "cells": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"archive_version": 0, "cells": []}
    if not isinstance(raw, dict):
        return {"archive_version": 0, "cells": []}
    return {
        "archive_version": int(raw.get("archive_version", 0) or 0),
        "cells": list(raw.get("cells") or []),
        "cache_stats": dict(raw.get("cache_stats") or {}),
    }


def save_local_manifest(local_root: Path | str, manifest: dict[str, Any]) -> None:
    path = local_manifest_path(local_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def manifest_client_from_env() -> _ManifestClient | None:
    """Build a learner HTTP client for lazy bundle fetch (worker subprocesses)."""
    host = (
        os.environ.get("RE1_LEARNER_HOST", "").strip()
        or os.environ.get("LEARNER_HOST", "").strip()
    )
    if not host:
        return None
    port_raw = (
        os.environ.get("RE1_LEARNER_PORT", "").strip()
        or os.environ.get("FLEET_LEARNER_PORT", "").strip()
        or "8765"
    )
    try:
        port = int(port_raw)
    except ValueError:
        port = 8765
    machine = os.environ.get("MACHINE_NAME", "worker").strip() or "worker"
    from re1_rl.distributed.worker_client import WorkerClient

    return WorkerClient(host, port, machine_name=machine, timeout=30.0)


def count_cached_bundles(local_root: Path | str) -> int:
    root = cells_dir(local_root)
    if not root.is_dir():
        return 0
    n = 0
    for p in root.iterdir():
        if not p.is_dir() or p.name.startswith("."):
            continue
        if (p / CELL_STATE_NAME).is_file() and (p / CELL_SIDECAR_NAME).is_file():
            n += 1
    return n


def prune_stale_cell_dirs(local_root: Path | str, valid_record_ids: set[str]) -> int:
    """Remove cached cell dirs not present in the authoritative manifest."""
    root = cells_dir(local_root)
    if not root.is_dir():
        return 0
    removed = 0
    for p in root.iterdir():
        if not p.is_dir() or p.name.startswith("."):
            continue
        if p.name not in valid_record_ids:
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    return removed


def _normalize_manifest_rows(remote_cells: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in remote_cells:
        if isinstance(row, dict) and row.get("record_id"):
            rows.append(dict(row))
    return rows


def poll_manifest(client: _ManifestClient, since_version: int, local_root: Path | str) -> dict[str, Any]:
    """Fetch learner manifest and mirror into ``local_manifest.json``.

    When the learner returns a non-empty ``cells`` list, treat it as a **full
    snapshot** (replace local rows, prune evicted cache dirs). When the learner
    reports ``cell_count`` that differs from the local mirror, force a full
    resync with ``since_version=0``.
    """
    root = go_explore_root(local_root)
    root.mkdir(parents=True, exist_ok=True)
    remote = client.fetch_go_explore_manifest(since_version=int(since_version))
    remote_ver = int(remote.get("archive_version", 0) or 0)
    remote_cells = list(remote.get("cells") or [])
    remote_cell_count = remote.get("cell_count")

    local = load_local_manifest(root)
    pruned = 0

    if remote_cells:
        rows = _normalize_manifest_rows(remote_cells)
        valid_ids = {str(r["record_id"]) for r in rows}
        pruned = prune_stale_cell_dirs(root, valid_ids)
        local = {
            "archive_version": remote_ver,
            "cells": rows,
        }
    elif remote_cell_count is not None:
        local_count = len(local.get("cells") or [])
        if int(remote_cell_count) != local_count and int(since_version) != 0:
            return poll_manifest(client, since_version=0, local_root=local_root)
        local["archive_version"] = remote_ver
    else:
        local["archive_version"] = remote_ver

    local["cache_stats"] = {
        "manifest_cells": len(local.get("cells") or []),
        "cached_bundles": count_cached_bundles(root),
        "pruned_dirs_last_poll": pruned,
        "remote_cell_count": int(remote_cell_count) if remote_cell_count is not None else None,
    }
    save_local_manifest(root, local)
    return local


def maybe_poll_manifest(
    client: _ManifestClient,
    local_root: Path | str,
    *,
    last_poll_mono: float,
    poll_s: float | None = None,
) -> float:
    """Poll learner manifest when interval elapsed. Returns updated monotonic timestamp."""
    import time

    interval = manifest_poll_s() if poll_s is None else float(poll_s)
    now = time.monotonic()
    if now - float(last_poll_mono) < interval:
        return float(last_poll_mono)
    local = load_local_manifest(local_root)
    since = int(local.get("archive_version", 0) or 0)
    poll_manifest(client, since_version=since, local_root=local_root)
    return now


def manifest_index_by_cell_key(local_root: Path | str) -> dict[str, dict[str, Any]]:
    """``cell_key`` → manifest row for capture dedupe before disk write."""
    out: dict[str, dict[str, Any]] = {}
    for row in load_local_manifest(local_root).get("cells") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("cell_key") or "").strip()
        if key:
            out[key] = row
    return out


def manifest_semantic_index(
    local_root: Path | str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """``(room_id, milestone_digest)`` → manifest rows for pose-cap pre-filter."""
    from re1_rl.go_explore_semantic import manifest_index_by_semantic_bucket

    return manifest_index_by_semantic_bucket(load_local_manifest(local_root))


def _local_meta_sha(cell_dir: Path) -> str | None:
    meta_p = cell_dir / CELL_META_NAME
    if not meta_p.is_file():
        return None
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sha = meta.get("bundle_sha256")
    return str(sha) if sha else None


def ensure_bundle_cached(
    client: _ManifestClient,
    record_id: str,
    local_root: Path | str,
    *,
    expected_sha256: str | None = None,
) -> Path | None:
    """Lazy-fetch a cell zip into ``cells/<record_id>/``. Returns cell dir or None."""
    rid = str(record_id)
    dest = cells_dir(local_root) / rid
    state_p = dest / CELL_STATE_NAME
    side_p = dest / CELL_SIDECAR_NAME
    if state_p.is_file() and side_p.is_file():
        if expected_sha256:
            local_sha = _local_meta_sha(dest)
            # Missing meta sha is a miss — files alone can be a rejected local
            # overwrite that must not masquerade as the learner bundle.
            if local_sha is not None and local_sha == str(expected_sha256):
                return dest
        else:
            return dest

    try:
        blob = client.fetch_go_explore_bundle(rid)
    except Exception:
        return None
    if not blob:
        return None

    incoming = dest.parent / f".incoming_{rid}"
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
            meta = {
                "record_id": rid,
                "bundle_sha256": (
                    str(expected_sha256)
                    if expected_sha256
                    else hashlib.sha256(blob).hexdigest()
                ),
                "bytes": len(blob),
            }
            meta_p.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(str(incoming), str(dest))
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        return None
    return dest


def resolve_local_bundle(local_root: Path | str, record_id: str) -> dict[str, str] | None:
    """Paths suitable for ``env.reset(options={\"pb_bundle\": ...})``."""
    d = cells_dir(local_root) / str(record_id)
    state_p = d / CELL_STATE_NAME
    side_p = d / CELL_SIDECAR_NAME
    if not state_p.is_file() or not side_p.is_file():
        return None
    return {
        "state_path": str(state_p),
        "sidecar_path": str(side_p),
        "record_id": str(record_id),
    }


def resolve_archive_bundle_for_reset(
    local_root: Path | str,
    row: dict[str, Any],
    *,
    client: _ManifestClient | None = None,
) -> dict[str, Any] | None:
    """Ensure bundle is cached locally, then return pb_bundle dict for reset."""
    rid = str(row.get("record_id") or "")
    if not rid:
        return None
    manifest_client = client or manifest_client_from_env()
    if manifest_client is not None:
        sha = str(row.get("bundle_sha256") or "") or None
        ensure_bundle_cached(
            manifest_client,
            rid,
            local_root,
            expected_sha256=sha,
        )
    resolved = resolve_local_bundle(local_root, rid)
    if resolved is None:
        return None
    out = dict(resolved)
    if row.get("cell_key"):
        out["milestone_id"] = str(row["cell_key"])
    return out
