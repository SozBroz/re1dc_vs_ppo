"""Per-version policy weight archive for mint recapture.

Every minted planner-loyal cell records the ``policy_version`` that ran the
episode (``meta.json`` → ``mint_policy.policy_version``) plus the full
per-step action distribution (``leg_policy.npz`` → ``masked_probs``). To
recompute ("recapture") the exact % chance for each action at each PPO step
offline — verification, counterfactuals, broadcast overlays — you need the
exact weights for that version.

This module persists the exact policy bytes each worker/learner loads
(``WeightStore`` broadcast bytes, so every box archives identical content)
under ``data/mint_policies/weights/v<V>.pt`` (gitignored, never committed).
Pruning keeps every version referenced by any cell meta plus the newest few,
so mint weights are never deleted while the archive stays bounded.

Fail-closed callers: every function swallows its own errors and returns a
bool/None so training can never break because archiving hiccuped.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

ARCHIVE_DIRNAME = "mint_policies"
WEIGHTS_DIRNAME = "weights"
# Newest unreferenced versions to retain (covers in-flight episodes).
KEEP_NEWEST_UNREFERENCED = 8
# Prune at most this often (seconds); archive writes are cheap, scans less so.
PRUNE_THROTTLE_S = 3600.0


def archive_root(project_root: Path | str | None) -> Path:
    base = Path(project_root) if project_root else Path.cwd()
    return base / "data" / ARCHIVE_DIRNAME


def weights_dir(project_root: Path | str | None) -> Path:
    return archive_root(project_root) / WEIGHTS_DIRNAME


def weight_path(project_root: Path | str | None, version: int) -> Path:
    return weights_dir(project_root) / f"v{int(version)}.pt"


def find_weight_file(project_root: Path | str | None, version: int) -> Path | None:
    """Return the archived weights for ``version``, or None on a miss."""
    try:
        path = weight_path(project_root, version)
    except (TypeError, ValueError):
        return None
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path
    except OSError:
        return None
    return None


def note_policy_version_weights(
    project_root: Path | str | None,
    version: int,
    policy_bytes: bytes,
) -> bool:
    """Archive the exact broadcast bytes for ``version`` (idempotent).

    Call after every successful weight pull / publish. Returns True when the
    version is on disk afterwards (already-there counts).
    """
    try:
        version = int(version)
        if version <= 0 or not policy_bytes:
            return False
        dest = weight_path(project_root, version)
        if dest.is_file() and dest.stat().st_size == len(policy_bytes):
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(f".tmp-{os.getpid()}.pt")
        tmp.write_bytes(bytes(policy_bytes))
        os.replace(tmp, dest)
        _maybe_prune(project_root)
        return True
    except (OSError, TypeError, ValueError):
        return False


def referenced_versions(project_root: Path | str | None) -> set[int]:
    """All ``mint_policy.policy_version`` values across planner-loyal metas."""
    keep: set[int] = set()
    try:
        base = Path(project_root) if project_root else Path.cwd()
        cells = base / "states" / "planner_loyal" / "cells"
        if not cells.is_dir():
            return keep
        for meta_path in cells.glob("pl*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            raw = (meta.get("mint_policy") or {}).get("policy_version")
            try:
                ver = int(raw)
            except (TypeError, ValueError):
                continue
            if ver > 0:
                keep.add(ver)
    except OSError:
        pass
    return keep


def prune_weight_archive(project_root: Path | str | None) -> dict[str, Any]:
    """Delete unreferenced weight files beyond the newest-retained window."""
    stats: dict[str, Any] = {"kept": 0, "deleted": 0, "bytes_freed": 0}
    try:
        wdir = weights_dir(project_root)
        if not wdir.is_dir():
            return stats
        keep = set(referenced_versions(project_root))
        files: list[tuple[int, Path]] = []
        for path in wdir.glob("v*.pt"):
            try:
                ver = int(path.stem[1:])
            except (TypeError, ValueError):
                continue
            files.append((ver, path))
        files.sort()
        newest = {ver for ver, _ in files[-int(KEEP_NEWEST_UNREFERENCED):]}
        for ver, path in files:
            if ver in keep or ver in newest:
                stats["kept"] += 1
                continue
            try:
                stats["bytes_freed"] += path.stat().st_size
                path.unlink()
                stats["deleted"] += 1
            except OSError:
                stats["kept"] += 1
    except OSError:
        pass
    return stats


def _maybe_prune(project_root: Path | str | None) -> None:
    try:
        marker = archive_root(project_root) / ".prune_tstamp"
        now = time.time()
        try:
            last = float(marker.read_text(encoding="utf-8").strip() or 0.0)
        except (OSError, ValueError, UnicodeDecodeError):
            last = 0.0
        if now - last < PRUNE_THROTTLE_S:
            return
        prune_weight_archive(project_root)
        try:
            marker.write_text(f"{now:.1f}\n", encoding="utf-8")
        except OSError:
            pass
    except (OSError, TypeError, ValueError):
        pass
