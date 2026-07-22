"""Async delayed sync of typewriter PB champions across fleet machines.

Local capture writes under ``RE1_PB_ROOT`` (default ``<project>/states/pb``).
When ``RE1_PB_SHARED_ROOT`` is set (e.g. ``Z:/re1_rl/states/pb`` on Samba),
a background thread periodically syncs **each champion slot independently**:

- **push** local slot → shared if local score is better for that slot
- **pull** shared slot → local if shared score is better for that slot

Never deletes champion trees. Sync lag is acceptable; training never blocks
on the shared FS.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from re1_rl.pb_champion import (
    CHAMPION_JSON,
    list_filled_champions,
    pb_root,
    score_beats,
    typewriter_champion_subdir,
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


def _score_version(rec: dict[str, Any] | None) -> int | None:
    if not rec or "score_version" not in rec:
        return None
    try:
        return int(rec["score_version"])
    except (TypeError, ValueError):
        return None


def _slot_name_from_subdir(subdir: str) -> str:
    """``champions/mainhall_typewriter`` → ``mainhall_typewriter``."""
    s = subdir.replace("\\", "/").strip("/")
    if s.startswith("champions/"):
        return s.split("/", 1)[1]
    return Path(s).name


def _is_slot_dirname(name: str) -> bool:
    return name == "mainhall_typewriter" or name.startswith("typewriter_")


def _scan_slot_names(pb_root_path: Path) -> set[str]:
    """Directory names under ``pb_root/champions/`` that look like typewriter slots."""
    champs = Path(pb_root_path) / "champions"
    if not champs.is_dir():
        return set()
    out: set[str] = set()
    for child in champs.iterdir():
        if child.is_dir() and _is_slot_dirname(child.name):
            out.add(child.name)
    return out


def _slot_names_from_filled(project_root: Path | str) -> set[str]:
    names: set[str] = set()
    try:
        for rec in list_filled_champions(project_root):
            room = rec.get("room_id")
            if room is not None:
                names.add(_slot_name_from_subdir(typewriter_champion_subdir(room)))
                continue
            cdir = rec.get("champion_dir")
            if cdir:
                names.add(Path(str(cdir)).name)
    except OSError:
        pass
    return names


def list_sync_slot_names(local_pb: Path, shared_pb: Path) -> list[str]:
    """Union of slot directory names present on local and/or shared trees."""
    names = _scan_slot_names(local_pb) | _scan_slot_names(shared_pb)
    return sorted(names)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _rel_paths_for_slot(slot_name: str) -> tuple[str, str]:
    subdir = f"champions/{slot_name}".replace("\\", "/")
    state = f"states/pb/{subdir}/champion.State"
    sidecar = f"states/pb/{subdir}/champion.sidecar.json"
    return state, sidecar


def _copy_champion_tree(src_dir: Path, dst_dir: Path, *, slot_name: str) -> None:
    """Copy champion.State / sidecar / json into *dst_dir* as one locked bundle.

    Never deletes unrelated files or other slot trees. Refuses to read a locked
    or incoherent source; installs under ``champion.sync.lock`` on the destination.
    """
    from re1_rl.pb_bundle_io import (
        install_champion_bundle,
        verify_champion_bundle,
        wait_for_slot_unlock,
    )

    ok, reason = verify_champion_bundle(src_dir, require_unlocked=True)
    if not ok:
        raise OSError(f"source champion incoherent ({reason}): {src_dir}")
    src_json = src_dir / CHAMPION_JSON
    if not src_json.is_file():
        return
    data = json.loads(src_json.read_text(encoding="utf-8"))
    state_rel, sidecar_rel = _rel_paths_for_slot(slot_name)
    data["state_path"] = state_rel
    data["sidecar_path"] = sidecar_rel
    cand_score = None
    try:
        cand_score = tuple(int(x) for x in (data.get("score") or ()))
    except (TypeError, ValueError):
        cand_score = None
    cand_ver = None
    try:
        if "score_version" in data:
            cand_ver = int(data["score_version"])
    except (TypeError, ValueError):
        cand_ver = None
    # Wait for dest lock, then CAS-install (None ⇒ dest already stronger — ok).
    if not wait_for_slot_unlock(dst_dir, timeout_s=90.0):
        raise OSError(f"destination champion locked: {dst_dir}")
    installed = install_champion_bundle(
        dst_dir,
        state_src=src_dir / "champion.State",
        sidecar_src=src_dir / "champion.sidecar.json",
        record=data,
        holder=f"pb_sync:{os.environ.get('COMPUTERNAME', 'local')}",
        bundle_id=str(data.get("bundle_id") or "") or None,
        candidate_score=cand_score if cand_score else None,
        candidate_version=cand_ver,
        wait_timeout_s=90.0,
    )
    if installed is None and cand_score:
        # Dest won the CAS — not an error; pull/push simply no-ops.
        return


def _sync_one_slot(
    local_dir: Path,
    shared_dir: Path,
    *,
    slot_name: str,
) -> dict[str, str]:
    """Push/pull a single slot. Never touches other slots."""
    actions: dict[str, str] = {"push": "skip", "pull": "skip"}

    local_rec = _read_json(local_dir / CHAMPION_JSON)
    shared_rec = _read_json(shared_dir / CHAMPION_JSON)
    local_score = _score_from_record(local_rec)
    shared_score = _score_from_record(shared_rec)
    local_ver = _score_version(local_rec)
    shared_ver = _score_version(shared_rec)
    # Missing version ⇒ treat as v1 (legacy).
    local_cand_ver = local_ver if local_ver is not None else 1
    shared_cand_ver = shared_ver if shared_ver is not None else 1

    from re1_rl.pb_bundle_io import verify_champion_bundle, wait_for_slot_unlock

    # Wait for locks rather than skipping the cycle — then re-read scores.
    if not wait_for_slot_unlock(local_dir, timeout_s=90.0):
        actions["push"] = "locked"
        actions["pull"] = "locked"
        return actions
    if not wait_for_slot_unlock(shared_dir, timeout_s=90.0):
        actions["push"] = "locked"
        actions["pull"] = "locked"
        return actions

    local_rec = _read_json(local_dir / CHAMPION_JSON)
    shared_rec = _read_json(shared_dir / CHAMPION_JSON)
    local_score = _score_from_record(local_rec)
    shared_score = _score_from_record(shared_rec)
    local_ver = _score_version(local_rec)
    shared_ver = _score_version(shared_rec)
    local_cand_ver = local_ver if local_ver is not None else 1
    shared_cand_ver = shared_ver if shared_ver is not None else 1

    local_ok, _ = verify_champion_bundle(local_dir, require_unlocked=True)
    if local_ok and local_rec:
        if score_beats(
            local_score or (),
            shared_score,
            candidate_version=local_cand_ver,
            incumbent_version=shared_ver,
        ):
            try:
                _copy_champion_tree(local_dir, shared_dir, slot_name=slot_name)
                actions["push"] = "ok"
            except (OSError, RuntimeError, ValueError) as exc:
                actions["push"] = f"error:{exc}"

    # Re-read shared after possible push.
    shared_rec = _read_json(shared_dir / CHAMPION_JSON)
    shared_score = _score_from_record(shared_rec)
    shared_ver = _score_version(shared_rec)
    shared_cand_ver = shared_ver if shared_ver is not None else 1

    shared_ok, _ = verify_champion_bundle(shared_dir, require_unlocked=True)
    if shared_ok and shared_rec:
        if score_beats(
            shared_score or (),
            local_score,
            candidate_version=shared_cand_ver,
            incumbent_version=local_ver,
        ):
            try:
                _copy_champion_tree(shared_dir, local_dir, slot_name=slot_name)
                actions["pull"] = "ok"
            except (OSError, RuntimeError, ValueError) as exc:
                actions["pull"] = f"error:{exc}"

    return actions


def sync_champion_once(project_root: Path | str) -> dict[str, Any]:
    """One push/pull cycle across **all** champion slots.

    Returns aggregate ``push`` / ``pull`` plus per-slot ``slots`` detail.
    Aggregate ``push``/``pull`` is ``ok`` if any slot succeeded, else ``skip``
    (or an ``error:…`` string if every attempted action failed).
    """
    actions: dict[str, Any] = {"push": "skip", "pull": "skip", "slots": {}}
    shared = shared_pb_root()
    if shared is None:
        return actions

    local_root = pb_root(project_root)
    # Prefer list_filled_champions for local, then union with directory scans.
    slot_names = (
        _slot_names_from_filled(project_root)
        | _scan_slot_names(local_root)
        | _scan_slot_names(shared)
    )
    if not slot_names:
        return actions

    any_push_ok = False
    any_pull_ok = False
    push_errors: list[str] = []
    pull_errors: list[str] = []

    for slot_name in sorted(slot_names):
        local_dir = local_root / "champions" / slot_name
        shared_dir = shared / "champions" / slot_name
        slot_actions = _sync_one_slot(local_dir, shared_dir, slot_name=slot_name)
        actions["slots"][slot_name] = slot_actions
        if slot_actions["push"] == "ok":
            any_push_ok = True
        elif str(slot_actions["push"]).startswith("error:"):
            push_errors.append(slot_actions["push"])
        if slot_actions["pull"] == "ok":
            any_pull_ok = True
        elif str(slot_actions["pull"]).startswith("error:"):
            pull_errors.append(slot_actions["pull"])

    if any_push_ok:
        actions["push"] = "ok"
    elif push_errors:
        actions["push"] = push_errors[0]
    if any_pull_ok:
        actions["pull"] = "ok"
    elif pull_errors:
        actions["pull"] = pull_errors[0]

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
    """Start background sync if ``RE1_PB_SHARED_ROOT`` is set. Idempotent.

    On first start, runs one synchronous ``sync_champion_once`` so a training
    restart can sample pulled sidecars on the first ``env.reset()``.
    """
    if shared_pb_root() is None:
        return False
    global _daemon
    with _daemon_lock:
        if _daemon is not None:
            return True
        try:
            sync_champion_once(project_root)
        except Exception:
            pass
        interval = float(os.environ.get(_SYNC_INTERVAL_ENV, _DEFAULT_INTERVAL_S) or 30)
        _daemon = _PbSyncDaemon(Path(project_root), interval)
        _daemon.start()
        return True


def warm_pb_champions_for_training(project_root: Path | str) -> dict[str, Any]:
    """Pull shared champions (when configured) and report reset-mix status.

    Call once at training process start (before actors spawn). Safe when no
    champions exist yet — mix is fresh-only until slots fill.
    """
    from re1_rl.pb_curriculum import typewriter_mix_weights

    root = Path(project_root)
    shared = shared_pb_root()
    sync_ran = False
    if shared is not None:
        sync_ran = ensure_pb_sync_daemon(root)
        if not sync_ran:
            try:
                sync_champion_once(root)
                sync_ran = True
            except Exception:
                pass
    filled = list_filled_champions(root)
    n = len(filled)
    p_fresh, p_each = typewriter_mix_weights(n)
    return {
        "n_filled": n,
        "p_fresh": p_fresh,
        "p_each_sidecar": p_each,
        "milestone_ids": [str(r.get("milestone_id") or "") for r in filled],
        "room_ids": [str(r.get("room_id") or "") for r in filled],
        "shared_root": str(shared) if shared is not None else None,
        "sync_ran": sync_ran,
    }


def push_champion_async(project_root: Path | str) -> None:
    """Fire-and-forget push/pull of all slots after a local champion replace."""
    if shared_pb_root() is None:
        return

    def _run() -> None:
        try:
            sync_champion_once(project_root)
        except Exception:
            pass

    threading.Thread(target=_run, name="re1-pb-sync-once", daemon=True).start()
