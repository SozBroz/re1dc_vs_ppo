"""Atomic PB champion install + coherence checks + sync lockfiles.

BizHawk ``champion.State`` and ``champion.sidecar.json`` must never be half-updated
relative to each other. Writers take ``champion.sync.lock``, stage into
``.incoming/``, then promote. Readers skip locked or incoherent slots.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from re1_rl.pb_champion import CHAMPION_JSON, pb_root

LOCK_NAME = "champion.sync.lock"
INCOMING_NAME = ".incoming"
STATE_NAME = "champion.State"
SIDECAR_NAME = "champion.sidecar.json"
STALE_LOCK_S = 180.0


def new_bundle_id() -> str:
    return uuid.uuid4().hex


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def lock_path(slot_dir: Path | str) -> Path:
    return Path(slot_dir) / LOCK_NAME


def clear_stale_lock(slot_dir: Path | str, *, stale_s: float = STALE_LOCK_S) -> bool:
    """Remove lock if missing host crashed mid-write. Returns True if removed."""
    lp = lock_path(slot_dir)
    if not lp.is_file():
        return False
    try:
        age = time.time() - lp.stat().st_mtime
    except OSError:
        return False
    if age < float(stale_s):
        return False
    try:
        lp.unlink()
        return True
    except OSError:
        return False


def is_slot_locked(slot_dir: Path | str, *, stale_s: float = STALE_LOCK_S) -> bool:
    clear_stale_lock(slot_dir, stale_s=stale_s)
    return lock_path(slot_dir).is_file()


def acquire_slot_lock(
    slot_dir: Path | str,
    *,
    holder: str,
    bundle_id: str | None = None,
    stale_s: float = STALE_LOCK_S,
) -> bool:
    """Create exclusive lock. Returns False if another fresh lock is held."""
    slot = Path(slot_dir)
    slot.mkdir(parents=True, exist_ok=True)
    clear_stale_lock(slot, stale_s=stale_s)
    lp = lock_path(slot)
    if lp.is_file():
        return False
    payload = {
        "holder": str(holder),
        "bundle_id": bundle_id,
        "created_unix": time.time(),
    }
    tmp = slot / f".{LOCK_NAME}.{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Exclusive create — fails if another writer raced us.
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(tmp.read_text(encoding="utf-8"))
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        return True
    except FileExistsError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def release_slot_lock(slot_dir: Path | str) -> None:
    try:
        lock_path(slot_dir).unlink()
    except OSError:
        pass


def clear_all_champion_locks(project_root: Path | str) -> int:
    """Delete every ``champion.sync.lock`` under champions/. Returns count removed."""
    champs = pb_root(project_root) / "champions"
    if not champs.is_dir():
        return 0
    n = 0
    for child in champs.iterdir():
        if not child.is_dir():
            continue
        lp = lock_path(child)
        if lp.is_file():
            try:
                lp.unlink()
                n += 1
            except OSError:
                pass
        incoming = child / INCOMING_NAME
        if incoming.is_dir():
            shutil.rmtree(incoming, ignore_errors=True)
    return n


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def verify_champion_bundle(
    slot_dir: Path | str,
    *,
    require_unlocked: bool = True,
    stale_s: float = STALE_LOCK_S,
) -> tuple[bool, str]:
    """Return ``(ok, reason)``. Fail closed on lock / missing half / id mismatch."""
    slot = Path(slot_dir)
    if require_unlocked and is_slot_locked(slot, stale_s=stale_s):
        return False, "locked"
    state_p = slot / STATE_NAME
    side_p = slot / SIDECAR_NAME
    json_p = slot / CHAMPION_JSON
    if not state_p.is_file():
        return False, "missing_state"
    if not side_p.is_file():
        return False, "missing_sidecar"
    if not json_p.is_file():
        return False, "missing_json"
    rec = _read_json(json_p)
    side = _read_json(side_p)
    if rec is None:
        return False, "bad_json"
    if side is None:
        return False, "bad_sidecar"
    bid_rec = rec.get("bundle_id")
    bid_side = side.get("bundle_id")
    if bid_rec or bid_side:
        if str(bid_rec or "") != str(bid_side or ""):
            return False, "bundle_id_mismatch"
    # Legacy champions (no bundle_id): still require both files; hash if present.
    state_sha = rec.get("state_sha256")
    if state_sha:
        try:
            if sha256_file(state_p) != str(state_sha):
                return False, "state_sha_mismatch"
        except OSError as exc:
            return False, f"state_sha_error:{exc}"
    side_sha = rec.get("sidecar_sha256")
    if side_sha:
        try:
            if sha256_file(side_p) != str(side_sha):
                return False, "sidecar_sha_mismatch"
        except OSError as exc:
            return False, f"sidecar_sha_error:{exc}"
    return True, "ok"


def install_champion_bundle(
    slot_dir: Path | str,
    *,
    state_src: Path | str,
    sidecar_src: Path | str,
    record: dict[str, Any],
    holder: str = "local",
    bundle_id: str | None = None,
) -> str:
    """Install State + sidecar + json under a lock. Returns ``bundle_id``.

    Stages into ``.incoming/`` then promotes with ``os.replace``. Lock is always
    released (best-effort) even on failure.
    """
    slot = Path(slot_dir)
    slot.mkdir(parents=True, exist_ok=True)
    state_src = Path(state_src)
    sidecar_src = Path(sidecar_src)
    if not state_src.is_file() or not sidecar_src.is_file():
        raise FileNotFoundError("install_champion_bundle requires State + sidecar sources")

    bid = bundle_id or new_bundle_id()
    if not acquire_slot_lock(slot, holder=holder, bundle_id=bid):
        raise RuntimeError(f"champion slot locked: {slot}")

    incoming = slot / INCOMING_NAME
    try:
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True, exist_ok=True)

        side = _read_json(sidecar_src)
        if side is None:
            raise ValueError(f"sidecar is not a JSON object: {sidecar_src}")
        side = dict(side)
        side["bundle_id"] = bid
        inc_state = incoming / STATE_NAME
        inc_side = incoming / SIDECAR_NAME
        inc_json = incoming / CHAMPION_JSON
        shutil.copy2(state_src, inc_state)
        inc_side.write_text(json.dumps(side, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        rec = dict(record)
        rec["bundle_id"] = bid
        rec["state_sha256"] = sha256_file(inc_state)
        rec["sidecar_sha256"] = sha256_file(inc_side)
        inc_json.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        ok, reason = verify_champion_bundle(incoming, require_unlocked=False)
        if not ok:
            raise RuntimeError(f"incoming champion incoherent: {reason}")

        # Promote payload files first; json last (readers key off json + hashes).
        os.replace(inc_state, slot / STATE_NAME)
        os.replace(inc_side, slot / SIDECAR_NAME)
        os.replace(inc_json, slot / CHAMPION_JSON)
        return bid
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
        release_slot_lock(slot)


def bundle_room_matches_sidecar(
    ram_room_id: str | None,
    sidecar: dict[str, Any],
) -> bool:
    """True when live RAM room matches sidecar capture room (when known)."""
    captured = sidecar.get("captured_room_id")
    if captured is None or captured == "":
        return True
    if ram_room_id is None or ram_room_id == "":
        return False
    return str(ram_room_id).strip().upper() == str(captured).strip().upper()
