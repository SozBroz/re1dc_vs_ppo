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
    }


def save_local_manifest(local_root: Path | str, manifest: dict[str, Any]) -> None:
    path = local_manifest_path(local_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def poll_manifest(client: _ManifestClient, since_version: int, local_root: Path | str) -> dict[str, Any]:
    """Fetch learner manifest and merge into ``local_manifest.json``.

    Returns the updated local manifest. When the learner reports no delta
    (``cells`` empty and version unchanged / already current), local index
    is left intact aside from refreshing ``archive_version``.
    """
    root = go_explore_root(local_root)
    root.mkdir(parents=True, exist_ok=True)
    remote = client.fetch_go_explore_manifest(since_version=int(since_version))
    remote_ver = int(remote.get("archive_version", 0) or 0)
    remote_cells = list(remote.get("cells") or [])

    local = load_local_manifest(root)
    if remote_cells:
        by_id = {
            str(c.get("record_id")): c
            for c in local.get("cells") or []
            if isinstance(c, dict) and c.get("record_id")
        }
        for row in remote_cells:
            if not isinstance(row, dict) or not row.get("record_id"):
                continue
            by_id[str(row["record_id"])] = row
        local = {
            "archive_version": remote_ver,
            "cells": list(by_id.values()),
        }
    else:
        local["archive_version"] = remote_ver
    save_local_manifest(root, local)
    return local


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
            if local_sha and local_sha == str(expected_sha256):
                return dest
            if local_sha is None and expected_sha256:
                # Have files but no meta — treat as hit if files present.
                return dest
            if local_sha == str(expected_sha256):
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
