"""Async delayed sync of the typewriter PB champion across fleet machines.

Local capture writes under ``RE1_PB_ROOT`` (default ``<project>/states/pb``).
When ``RE1_PB_SHARED_ROOT`` is set (e.g. ``Z:/re1_rl/states/pb`` on Samba),
a background thread periodically:

- **push** local champion → shared if local score is better
- **pull** shared champion → local if shared score is better

Sync lag is acceptable; training never blocks on the shared FS.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from re1_rl.pb_champion import (
    CHAMPION_JSON,
    CHAMPION_SUBDIR,
    pb_root,
    score_beats,
)

_SHARED_ENV = "RE1_PB_SHARED_ROOT"
_SYNC_INTERVAL_ENV = "RE1_PB_SYNC_INTERVAL_S"
_DEFAULT_INTERVAL_S = 30.0

_daemon_lock = threading.Lock()
_daemon: "_PbSyncDaemon | None" = None


def shared_pb_root() -> Path | None:
    raw = os.environ.get(_SHARED_ENV, "").strip()
    if not raw:
        return None
    return Path(raw)


def _score_from_record(rec: dict[str, Any] | None) -> tuple[int, ...] | None:
    if not rec or "score" not in rec:
        return None
    try:
        return tuple(int(x) for x in rec["score"])
    except (TypeError, ValueError):
        return None


def _champion_payload_dir(root: Path) -> Path:
    return Path(root) / CHAMPION_SUBDIR


def _copy_champion_tree(src_dir: Path, dst_dir: Path) -> None:
    """Copy champion.State / sidecar / json into *dst_dir* (atomic json last)."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in ("champion.State", "champion.sidecar.json"):
        src = src_dir / name
        if src.is_file():
            shutil.copy2(src, dst_dir / name)
    src_json = src_dir / CHAMPION_JSON
    if not src_json.is_file():
        return
    data = json.loads(src_json.read_text(encoding="utf-8"))
    # Project-relative paths (local and shared trees use the same layout).
    data["state_path"] = f"states/pb/{CHAMPION_SUBDIR}/champion.State".replace(
        "\\", "/"
    )
    data["sidecar_path"] = (
        f"states/pb/{CHAMPION_SUBDIR}/champion.sidecar.json".replace("\\", "/")
    )
    tmp = dst_dir / f".{CHAMPION_JSON}.tmp"
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, dst_dir / CHAMPION_JSON)


def sync_champion_once(project_root: Path | str) -> dict[str, str]:
    """One push/pull cycle. Returns actions taken: push/pull/none keys."""
    actions: dict[str, str] = {"push": "skip", "pull": "skip"}
    shared = shared_pb_root()
    if shared is None:
        return actions

    local_root = pb_root(project_root)
    local_dir = _champion_payload_dir(local_root)
    shared_dir = _champion_payload_dir(shared)

    local_rec = None
    local_json = local_dir / CHAMPION_JSON
    if local_json.is_file():
        try:
            local_rec = json.loads(local_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            local_rec = None
    shared_rec = None
    shared_json = shared_dir / CHAMPION_JSON
    if shared_json.is_file():
        try:
            shared_rec = json.loads(shared_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            shared_rec = None

    local_score = _score_from_record(local_rec)
    shared_score = _score_from_record(shared_rec)

    # Push if local strictly better (or shared missing).
    if local_rec and local_dir.joinpath("champion.State").is_file():
        if score_beats(local_score or (), shared_score):
            try:
                _copy_champion_tree(local_dir, shared_dir)
                actions["push"] = "ok"
            except OSError as exc:
                actions["push"] = f"error:{exc}"

    # Re-read shared after possible push.
    if shared_json.is_file():
        try:
            shared_rec = json.loads(shared_json.read_text(encoding="utf-8"))
            shared_score = _score_from_record(shared_rec)
        except (OSError, json.JSONDecodeError):
            pass

    if shared_rec and shared_dir.joinpath("champion.State").is_file():
        if score_beats(shared_score or (), local_score):
            try:
                _copy_champion_tree(shared_dir, local_dir)
                data = json.loads(
                    (local_dir / CHAMPION_JSON).read_text(encoding="utf-8")
                )
                data["state_path"] = (
                    f"states/pb/{CHAMPION_SUBDIR}/champion.State".replace("\\", "/")
                )
                data["sidecar_path"] = (
                    f"states/pb/{CHAMPION_SUBDIR}/champion.sidecar.json".replace(
                        "\\", "/"
                    )
                )
                (local_dir / CHAMPION_JSON).write_text(
                    json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                actions["pull"] = "ok"
            except OSError as exc:
                actions["pull"] = f"error:{exc}"

    return actions


class _PbSyncDaemon:
    def __init__(self, project_root: Path, interval_s: float) -> None:
        self.project_root = Path(project_root)
        self.interval_s = max(5.0, float(interval_s))
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop,
            name="re1-pb-sync",
            daemon=True,
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                sync_champion_once(self.project_root)
            except Exception:
                continue


def ensure_pb_sync_daemon(project_root: Path | str) -> bool:
    """Start background sync if ``RE1_PB_SHARED_ROOT`` is set. Idempotent."""
    if shared_pb_root() is None:
        return False
    global _daemon
    with _daemon_lock:
        if _daemon is not None:
            return True
        interval = float(os.environ.get(_SYNC_INTERVAL_ENV, _DEFAULT_INTERVAL_S) or 30)
        _daemon = _PbSyncDaemon(Path(project_root), interval)
        _daemon.start()
        return True


def push_champion_async(project_root: Path | str) -> None:
    """Fire-and-forget push/pull after a local champion replace."""
    if shared_pb_root() is None:
        return

    def _run() -> None:
        try:
            sync_champion_once(project_root)
        except Exception:
            pass

    threading.Thread(target=_run, name="re1-pb-sync-once", daemon=True).start()
