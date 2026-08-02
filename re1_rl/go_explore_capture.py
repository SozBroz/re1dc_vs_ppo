"""Go-Explore cell capture: integrity gate, quality, atomic bundle, HTTP proposal."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import time
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from re1_rl.go_explore_archive import (
    GoExploreArchive,
    Quality,
    new_record_id,
    quality_beats,
)
from re1_rl.item_todo import canonical_item
from re1_rl.milestone_digest import (
    DEFAULT_TILE_SPAN,
    cell_key_v2,
    compute_digest,
)
from re1_rl.pb_bundle_io import (
    acquire_slot_lock,
    new_bundle_id,
    release_slot_lock,
    sha256_file,
    wait_for_slot_unlock,
)
from re1_rl.pb_sidecar import (
    EpisodeSidecarParts,
    dump_episode_sidecar,
    utc_now_iso,
)
from re1_rl.progress import ProgressTracker

_CAPTURE_ENV_VAR = "RE1_GO_EXPLORE_CAPTURE"
_ARCHIVE_ENV_VAR = "RE1_GO_EXPLORE_ARCHIVE"
_COOLDOWN_STEPS_ENV = "RE1_GO_CAPTURE_COOLDOWN_STEPS"
_MIN_FREE_GB_ENV = "RE1_GO_MIN_FREE_GB"
_REPLACE_BUDGET_DAY_ENV = "RE1_GO_REPLACE_BUDGET_DAY"
_MAX_CAPTURES_DAY_ENV = "RE1_GO_MAX_CAPTURES_DAY"
_MAX_CAPTURE_BYTES_DAY_ENV = "RE1_GO_MAX_CAPTURE_BYTES_DAY"
_CANONICAL_STORE_ENV = "RE1_GO_CANONICAL_STORE"
_MIN_HP_DELTA_ENV = "RE1_GO_REPLACE_MIN_HP_DELTA"
_MIN_AMMO_DELTA_ENV = "RE1_GO_REPLACE_MIN_AMMO_DELTA"
_DEFAULT_COOLDOWN_STEPS = 60
_DEFAULT_MIN_FREE_GB = 10.0
_DEFAULT_REPLACE_BUDGET_DAY = 200
_DEFAULT_MAX_CAPTURES_DAY = 200
_DEFAULT_MIN_HP_DELTA = 5
_DEFAULT_MIN_AMMO_DELTA = 10
_MAX_BUNDLE_BYTES_ENV = "RE1_GO_MAX_CAPTURE_BUNDLE_BYTES"
_DEFAULT_MAX_BUNDLE_BYTES = 5_000_000
_BUDGET_LOCK_NAME = "capture_budget.lock"
_BUDGET_FILE_NAME = "capture_budget.json"
_BUDGET_SCHEMA_VERSION = 1
_BUDGET_STALE_LOCK_S = 180.0
_PURGE_DONE: set[str] = set()

CELL_STATE_NAME = "cell.State"
CELL_SIDECAR_NAME = "cell.sidecar.json"
CELL_META_NAME = "meta.json"
INCOMING_NAME = ".incoming"

# Ammo names counted into the quality ammo component.
_AMMO_NAMES: frozenset[str] = frozenset(
    {
        "handgun_bullets",
        "shotgun_shells",
        "magnum_rounds",
        "dumdum_rounds",
        "flamethrower_fuel",
        "explosive_rounds",
        "acid_rounds",
        "flame_rounds",
        "rocket",
        "beretta",  # loaded rounds live in qty
        "shotgun",
        "colt_python",
        "colt_python_dumdum",
        "flamethrower",
        "bazooka_acid",
        "bazooka_explosive",
        "bazooka_flame",
        "rocket_launcher",
    }
)

# Healing / cure stacks (qty summed).
_HEALING_NAMES: frozenset[str] = frozenset(
    {
        "green_herb",
        "first_aid_spray",
        "first_aid_spray_alt",
        "serum",
        "mixed_herbs_gr",
        "mixed_herbs_gg",
        "mixed_herbs_gb",
        "mixed_herbs_grb",
        "mixed_herbs_ggg",
        "mixed_herbs_ggb",
        "blue_herb",  # poison cure counts as healing resource
    }
)

SaveStateCallback = Callable[[Path], None]


def go_explore_capture_enabled() -> bool:
    """True when ``RE1_GO_EXPLORE_CAPTURE=1`` (default off)."""
    return os.environ.get(_CAPTURE_ENV_VAR, "").strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }


def resolve_archive_path(project_root: Path | str | None = None) -> Path:
    """Absolute path to ``archive.json`` (never relative to BizHawk cwd)."""
    root = Path(project_root) if project_root is not None else Path.cwd()
    override = os.environ.get(_ARCHIVE_ENV_VAR, "").strip()
    if override:
        p = Path(override)
        if not p.is_absolute():
            p = root / p
        return p.resolve()
    return (root / "data" / "go_explore" / "archive.json").resolve()


def go_explore_root(project_root: Path | str | None = None) -> Path:
    """``data/go_explore`` under project root (or parent of archive path)."""
    return resolve_archive_path(project_root).parent


def cells_root(project_root: Path | str | None = None, *, archive: GoExploreArchive | None = None) -> Path:
    if archive is not None:
        return archive.path.resolve().parent / "cells"
    return go_explore_root(project_root) / "cells"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def capture_cooldown_steps() -> int:
    return max(1, _env_int(_COOLDOWN_STEPS_ENV, _DEFAULT_COOLDOWN_STEPS))


def min_free_bytes() -> int:
    gb = max(0.0, _env_float(_MIN_FREE_GB_ENV, _DEFAULT_MIN_FREE_GB))
    return int(gb * 1024 * 1024 * 1024)


def replace_budget_per_day() -> int:
    return max(0, _env_int(_REPLACE_BUDGET_DAY_ENV, _DEFAULT_REPLACE_BUDGET_DAY))


def max_captures_per_day() -> int:
    raw = os.environ.get(_MAX_CAPTURES_DAY_ENV, "").strip()
    if raw:
        return max(0, _env_int(_MAX_CAPTURES_DAY_ENV, _DEFAULT_MAX_CAPTURES_DAY))
    return max(0, _env_int(_REPLACE_BUDGET_DAY_ENV, _DEFAULT_REPLACE_BUDGET_DAY))


def max_capture_bytes_per_day() -> int:
    raw = os.environ.get(_MAX_CAPTURE_BYTES_DAY_ENV, "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def canonical_store_enabled() -> bool:
    """When true, workers install under ``cells/`` locally (learner/tests only)."""
    return os.environ.get(_CANONICAL_STORE_ENV, "").strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }


def capture_budget_path(project_root: Path | str | None = None) -> Path:
    return go_explore_root(project_root) / _BUDGET_FILE_NAME


def _capture_budget_lock_path(project_root: Path | str | None = None) -> Path:
    return go_explore_root(project_root) / _BUDGET_LOCK_NAME


def _clear_stale_budget_lock(project_root: Path | str | None) -> bool:
    lp = _capture_budget_lock_path(project_root)
    if not lp.is_file():
        return False
    try:
        age = time.time() - lp.stat().st_mtime
    except OSError:
        return False
    if age < _BUDGET_STALE_LOCK_S:
        return False
    try:
        lp.unlink()
        return True
    except OSError:
        return False


def _acquire_capture_budget_lock(
    project_root: Path | str | None,
    *,
    holder: str = "capture_budget",
) -> bool:
    root = go_explore_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    _clear_stale_budget_lock(project_root)
    lp = _capture_budget_lock_path(project_root)
    if lp.is_file():
        return False
    payload = json.dumps(
        {"holder": holder, "created_unix": time.time(), "pid": os.getpid()},
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            os.close(fd)
            raise
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def _release_capture_budget_lock(project_root: Path | str | None) -> None:
    try:
        _capture_budget_lock_path(project_root).unlink(missing_ok=True)
    except OSError:
        pass


def _load_capture_budget(project_root: Path | str | None) -> dict[str, Any] | None:
    path = capture_budget_path(project_root)
    if not path.is_file():
        return {
            "schema_version": _BUDGET_SCHEMA_VERSION,
            "day": date.today().isoformat(),
            "captures": 0,
            "bytes": 0,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _save_capture_budget(project_root: Path | str | None, row: dict[str, Any]) -> None:
    path = capture_budget_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    payload = dict(row)
    payload["schema_version"] = _BUDGET_SCHEMA_VERSION
    payload["updated_at_iso"] = utc_now_iso()
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _rollover_budget_row(row: dict[str, Any]) -> dict[str, Any]:
    today = date.today().isoformat()
    if str(row.get("day") or "") != today:
        return {
            "schema_version": _BUDGET_SCHEMA_VERSION,
            "day": today,
            "captures": 0,
            "bytes": 0,
        }
    return {
        "schema_version": _BUDGET_SCHEMA_VERSION,
        "day": today,
        "captures": int(row.get("captures", 0) or 0),
        "bytes": int(row.get("bytes", 0) or 0),
    }


def max_capture_bundle_bytes() -> int:
    raw = os.environ.get(_MAX_BUNDLE_BYTES_ENV, "").strip()
    if not raw:
        return int(_DEFAULT_MAX_BUNDLE_BYTES)
    try:
        return max(1, int(raw))
    except ValueError:
        return int(_DEFAULT_MAX_BUNDLE_BYTES)


def capture_budget_available(
    project_root: Path | str | None,
    *,
    min_bytes: int = 0,
) -> bool:
    """True when a capture may still be attempted under daily caps (no reserve).

    Fail-closed on corrupt/missing readable budget. Used to skip ``save_state``
    after the day cap is hit — budget consume still runs after the bundle exists.
    """
    max_count = max_captures_per_day()
    max_bytes = max_capture_bytes_per_day()
    if max_count <= 0 and max_bytes <= 0:
        return False
    loaded = _load_capture_budget(project_root)
    if loaded is None:
        return False
    row = _rollover_budget_row(loaded)
    captures = int(row["captures"])
    used_bytes = int(row["bytes"])
    if max_count > 0 and captures >= max_count:
        return False
    if max_bytes > 0 and used_bytes >= max_bytes:
        return False
    if max_bytes > 0 and used_bytes + max(0, int(min_bytes)) > max_bytes:
        return False
    return True


def try_consume_capture_budget(
    project_root: Path | str | None,
    *,
    nbytes: int = 0,
) -> bool:
    """Reserve one capture (new or replace) under machine-wide daily caps."""
    max_count = max_captures_per_day()
    max_bytes = max_capture_bytes_per_day()
    if max_count <= 0 and max_bytes <= 0:
        return False
    add_bytes = max(0, int(nbytes))
    if not _acquire_capture_budget_lock(project_root):
        return False
    try:
        loaded = _load_capture_budget(project_root)
        if loaded is None:
            return False
        row = _rollover_budget_row(loaded)
        captures = int(row["captures"])
        used_bytes = int(row["bytes"])
        if max_count > 0 and captures >= max_count:
            return False
        if max_bytes > 0 and used_bytes + add_bytes > max_bytes:
            return False
        row["captures"] = captures + 1
        row["bytes"] = used_bytes + add_bytes
        _save_capture_budget(project_root, row)
        return True
    finally:
        _release_capture_budget_lock(project_root)


def refund_capture_budget(
    project_root: Path | str | None,
    *,
    nbytes: int = 0,
) -> None:
    """Best-effort undo after a reserved capture failed before durable install."""
    if not _acquire_capture_budget_lock(project_root):
        return
    try:
        loaded = _load_capture_budget(project_root)
        if loaded is None:
            return
        row = _rollover_budget_row(loaded)
        row["captures"] = max(0, int(row.get("captures", 0) or 0) - 1)
        row["bytes"] = max(0, int(row.get("bytes", 0) or 0) - max(0, int(nbytes)))
        _save_capture_budget(project_root, row)
    finally:
        _release_capture_budget_lock(project_root)


def adjust_capture_budget_bytes(
    project_root: Path | str | None,
    *,
    delta_bytes: int,
) -> None:
    """Patch byte tally when actual bundle size differs from the reservation."""
    if delta_bytes == 0:
        return
    if not _acquire_capture_budget_lock(project_root):
        return
    try:
        loaded = _load_capture_budget(project_root)
        if loaded is None:
            return
        row = _rollover_budget_row(loaded)
        row["bytes"] = max(0, int(row.get("bytes", 0) or 0) + int(delta_bytes))
        _save_capture_budget(project_root, row)
    finally:
        _release_capture_budget_lock(project_root)


def _min_hp_delta() -> int:
    return max(1, _env_int(_MIN_HP_DELTA_ENV, _DEFAULT_MIN_HP_DELTA))


def _min_ammo_delta() -> int:
    return max(1, _env_int(_MIN_AMMO_DELTA_ENV, _DEFAULT_MIN_AMMO_DELTA))


def _disk_free_bytes(path: Path) -> int:
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(str(probe))
        return int(usage.free)
    except OSError:
        return 0


def quality_replace_significant(
    new_q: Quality,
    old_q: Quality | list[int] | tuple[int, ...],
) -> bool:
    """Ignore HP/ammo noise; require meaningful survival-resource gains."""
    o = tuple(int(x) for x in old_q)
    n = tuple(int(x) for x in new_q)
    while len(o) < 5:
        o = o + (0,)
    while len(n) < 5:
        n = n + (0,)
    if n[0] - o[0] >= _min_hp_delta():
        return True
    if n[1] - o[1] >= _min_ammo_delta():
        return True
    if n[2] > o[2]:
        return True
    if n[3] > o[3]:
        return True
    if n[4] > o[4]:
        return True
    return False


def _touch_replace_budget(capture_state: dict[str, Any] | None) -> bool:
    """Legacy per-env replace counter — superseded by ``try_consume_capture_budget``."""
    _ = capture_state
    return True


def _manifest_entry(
    manifest_index: dict[str, dict[str, Any]] | None,
    archive: GoExploreArchive,
    key: str,
) -> tuple[Any | None, str | None]:
    if manifest_index is not None:
        row = manifest_index.get(key)
        if not isinstance(row, dict):
            return None, None
        q_raw = row.get("quality")
        if isinstance(q_raw, (list, tuple)) and len(q_raw) >= 5:
            quality = tuple(int(x) for x in q_raw[:5])
        else:
            quality = None
        rid = str(row.get("record_id") or "") or None
        return quality, rid
    existing = archive.cells.get(key)
    if existing is None:
        return None, None
    return existing.quality, existing.record_id


def _room_digest_count(
    room: str,
    *,
    manifest_index: dict[str, dict[str, Any]] | None,
    archive: GoExploreArchive,
) -> int:
    from re1_rl.go_explore_semantic import room_digest_count

    return room_digest_count(
        room,
        manifest_index=manifest_index,
        archive_cells=archive.cells.values() if manifest_index is None else None,
    )


def _build_ephemeral_proposal(
    *,
    key: str,
    record_id: str,
    room: str,
    quality: Quality,
    digest: str,
    span: int,
    x: int,
    z: int,
    captured_at: str,
    state_tmp: Path,
    side_tmp: Path,
    reason: str,
    capture_reasons: list[str] | None = None,
) -> dict[str, Any] | None:
    from re1_rl.go_explore_merge import (
        CELL_SIDECAR_NAME,
        CELL_STATE_NAME,
        make_cell_bundle_zip,
    )

    state_bytes = state_tmp.read_bytes()
    sidecar = json.loads(side_tmp.read_text(encoding="utf-8"))
    reasons = list(capture_reasons or [])
    meta = {
        "record_id": record_id,
        "cell_key": key,
        "room_id": room,
        "quality": list(quality),
        "milestone_digest": digest,
        "captured_at_iso": captured_at,
        "tile_span": span,
        "x": x,
        "z": z,
        "integrity": reason,
        "capture_reasons": reasons,
    }
    blob = make_cell_bundle_zip(state_bytes=state_bytes, sidecar=sidecar, meta=meta)
    # Hash zip members, not temp files: on Windows write_text() CRLF sidecars do not
    # match LF bytes inside the zip (learner validates zip contents).
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        state_sha = hashlib.sha256(zf.read(CELL_STATE_NAME)).hexdigest()
        side_sha = hashlib.sha256(zf.read(CELL_SIDECAR_NAME)).hexdigest()
    return {
        "cell_key": key,
        "record_id": record_id,
        "quality": list(quality),
        "room_id": room,
        "milestone_digest": digest,
        "bundle_b64": base64.b64encode(blob).decode("ascii"),
        "state_sha256": state_sha,
        "sidecar_sha256": side_sha,
        "captured_at_iso": captured_at,
        "bundle_path": f"cells/{record_id}",
        "capture_reasons": reasons,
        "meta": {"capture_reasons": reasons},
    }


def purge_orphan_cell_dirs(
    cells_root_path: Path | str,
    *,
    known_record_ids: set[str] | frozenset[str] | None = None,
) -> int:
    """Remove ``.staging`` debris and cell dirs without an installed bundle.

    Returns count of removed top-level cell directories.
    """
    root = Path(cells_root_path)
    if not root.is_dir():
        return 0
    removed = 0
    known = {str(r) for r in (known_record_ids or ())}
    for child in list(root.iterdir()):
        if child.is_dir() and child.name.startswith(".staging"):
            shutil.rmtree(child, ignore_errors=True)
            continue
        if not child.is_dir():
            continue
        staging = child / ".staging"
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
        state_p = child / CELL_STATE_NAME
        side_p = child / CELL_SIDECAR_NAME
        installed = state_p.is_file() and side_p.is_file()
        if installed:
            continue
        if known and child.name in known:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed


def ensure_cells_root_purged(
    cells_root_path: Path | str,
    *,
    known_record_ids: set[str] | frozenset[str] | None = None,
) -> int:
    """Run orphan purge once per process per cells root."""
    key = str(Path(cells_root_path).resolve())
    if key in _PURGE_DONE:
        return 0
    _PURGE_DONE.add(key)
    return purge_orphan_cell_dirs(cells_root_path, known_record_ids=known_record_ids)


def _inventory_slots(state: dict[str, Any]) -> list[tuple[str, int]]:
    raw = state.get("inventory_slots")
    if raw is None:
        return [
            (canonical_item(str(n)), 1)
            for n in (state.get("inventory") or [])
            if n
        ]
    out: list[tuple[str, int]] = []
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            out.append((canonical_item(str(entry[0])), int(entry[1])))
        elif isinstance(entry, dict):
            out.append(
                (
                    canonical_item(
                        str(entry.get("name") or entry.get("item") or "")
                    ),
                    int(entry.get("qty", 1) or 0),
                )
            )
    return out


def compute_quality(state: dict[str, Any]) -> Quality:
    """Lexicographic quality: ``(hp, ammo, healing, slots, poison)``.

    ``poison`` is ``1`` when healthy, ``0`` when poisoned (higher is better).
    """
    hp = int(state.get("hp", 0) or 0)
    ammo = 0
    healing = 0
    slots = 0
    for name, qty in _inventory_slots(state):
        q = max(0, int(qty))
        if not name or q <= 0:
            continue
        slots += 1
        if name in _AMMO_NAMES:
            ammo += q
        if name in _HEALING_NAMES:
            healing += q
    poisoned = bool(state.get("poisoned")) or bool(
        int(state.get("player_poison", 0) or 0)
    )
    poison_ok = 0 if poisoned else 1
    return (hp, ammo, healing, slots, poison_ok)


def integrity_gate_ok(
    state: dict[str, Any],
    progress: ProgressTracker,
) -> tuple[bool, str]:
    """Admit only stable, controllable, non-terminal states."""
    if not bool(state.get("in_control")):
        return False, "not_in_control"
    if bool(state.get("dead")):
        return False, "dead"
    if int(state.get("hp", 0) or 0) <= 0:
        return False, "hp_zero"
    if bool(getattr(progress, "kenneth_gate_breached", False)):
        return False, "kenneth_gate_breached"
    room = str(state.get("room_id", "") or "").strip().upper()
    if not room:
        return False, "missing_room"
    # Stable room: reject explicit transition / unsettled markers when present.
    if bool(state.get("room_transition")) or bool(state.get("door_transition")):
        return False, "room_transition"
    if state.get("stable_room") is False:
        return False, "unstable_room"
    return True, "ok"


def _ever_held_from(
    ever_held: Iterable[str] | None,
    env: Any,
) -> set[str]:
    if ever_held is not None:
        return {canonical_item(str(n)) for n in ever_held if n}
    items = getattr(env, "_items", None) if env is not None else None
    held = getattr(items, "ever_held", None) if items is not None else None
    return {canonical_item(str(n)) for n in (held or ()) if n}


def _dump_sidecar(
    *,
    env: Any,
    progress: ProgressTracker,
    state: dict[str, Any],
    captured_at: str,
) -> dict[str, Any]:
    if env is not None and not isinstance(env, EpisodeSidecarParts):
        return dump_episode_sidecar(
            env,
            captured_room_id=str(state.get("room_id", "") or ""),
            captured_at_iso=captured_at,
        )
    parts = env if isinstance(env, EpisodeSidecarParts) else None
    if parts is None:
        from re1_rl.episode_history import EpisodeHistory
        from re1_rl.item_todo import ItemTracker

        parts = EpisodeSidecarParts(
            progress=progress,
            items=ItemTracker(todo=[]),
            episode_history=EpisodeHistory(),
        )
    return dump_episode_sidecar(
        parts,
        captured_room_id=str(state.get("room_id", "") or ""),
        captured_at_iso=captured_at,
    )


def install_cell_bundle(
    cell_dir: Path | str,
    *,
    state_src: Path | str,
    sidecar_src: Path | str,
    meta: dict[str, Any],
    holder: str = "go_explore_capture",
    wait_timeout_s: float = 90.0,
) -> dict[str, str]:
    """Atomically install ``cell.State`` + sidecar + ``meta.json``.

    Returns sha256 digests for state and sidecar.
    """
    slot = Path(cell_dir)
    slot.mkdir(parents=True, exist_ok=True)
    state_src = Path(state_src)
    sidecar_src = Path(sidecar_src)
    if not state_src.is_file() or not sidecar_src.is_file():
        raise FileNotFoundError("install_cell_bundle requires State + sidecar sources")

    if not wait_for_slot_unlock(slot, timeout_s=wait_timeout_s):
        raise RuntimeError(f"cell slot locked (timeout): {slot}")
    if not acquire_slot_lock(slot, holder=holder, bundle_id=meta.get("record_id")):
        if not wait_for_slot_unlock(slot, timeout_s=min(30.0, wait_timeout_s)):
            raise RuntimeError(f"cell slot locked: {slot}")
        if not acquire_slot_lock(slot, holder=holder, bundle_id=meta.get("record_id")):
            raise RuntimeError(f"cell slot locked: {slot}")

    incoming = slot / INCOMING_NAME
    try:
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True, exist_ok=True)

        inc_state = incoming / CELL_STATE_NAME
        inc_side = incoming / CELL_SIDECAR_NAME
        inc_meta = incoming / CELL_META_NAME
        shutil.copy2(state_src, inc_state)
        shutil.copy2(sidecar_src, inc_side)

        state_sha = sha256_file(inc_state)
        side_sha = sha256_file(inc_side)
        rec = dict(meta)
        rec["state_sha256"] = state_sha
        rec["sidecar_sha256"] = side_sha
        rec.setdefault("bundle_id", new_bundle_id())
        inc_meta.write_text(
            json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        os.replace(inc_state, slot / CELL_STATE_NAME)
        os.replace(inc_side, slot / CELL_SIDECAR_NAME)
        os.replace(inc_meta, slot / CELL_META_NAME)
        return {"state_sha256": state_sha, "sidecar_sha256": side_sha}
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
        release_slot_lock(slot)


def maybe_capture_cell(
    env_state: dict[str, Any],
    progress: ProgressTracker,
    archive: GoExploreArchive,
    *,
    save_state: SaveStateCallback,
    ever_held: Iterable[str] | None = None,
    env: Any = None,
    project_root: Path | str | None = None,
    tile_span: int | None = None,
    path_rooms: frozenset[str] | set[str] | None = None,
    env_step: int = 0,
    capture_state: dict[str, Any] | None = None,
    manifest_index: dict[str, dict[str, Any]] | None = None,
    semantic_index: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    persist_locally: bool | None = None,
    capture_reasons: list[str] | tuple[str, ...] | None = None,
    require_reason: bool | None = None,
) -> dict[str, Any] | None:
    """Capture a Go-Explore cell when integrity + quality gates pass.

    Workers default to ephemeral capture (zip in proposal, no local ``cells/``).
    Set ``RE1_GO_CANONICAL_STORE=1`` to install bundles locally (learner/tests).

    When ``require_reason`` is True, capture only fires if ``capture_reasons`` is
    non-empty (progress / coverage / quality upgrade). Default: require a reason
    when ``capture_reasons`` is explicitly passed; otherwise admit (unit tests).
    """
    if not go_explore_capture_enabled():
        return None

    ok, reason = integrity_gate_ok(env_state, progress)
    if not ok:
        return None

    room = str(env_state.get("room_id", "") or "").strip().upper()
    if not room:
        return None
    # Optional allowlist; default admits any legal room (progress-gated upstream).
    if path_rooms is not None and room not in {_normalize_room(r) for r in path_rooms}:
        return None

    reasons = [str(r) for r in (capture_reasons or ()) if r]
    must_reason = (
        bool(require_reason)
        if require_reason is not None
        else capture_reasons is not None
    )
    if must_reason and not reasons:
        return None
    priority = bool(reasons)

    x = int(env_state.get("x", env_state.get("player_x", 0)) or 0)
    z = int(env_state.get("z", env_state.get("player_z", 0)) or 0)
    span = int(tile_span if tile_span is not None else archive.tile_span or DEFAULT_TILE_SPAN)

    held = _ever_held_from(ever_held, env)
    digest = compute_digest(env_state, progress, ever_held=held)
    key = cell_key_v2(room, x, z, digest, tile_span=span)
    quality = compute_quality(env_state)

    existing_quality, existing_record_id = _manifest_entry(manifest_index, archive, key)
    is_replace = existing_quality is not None

    from re1_rl.go_explore_semantic import (
        manifest_index_by_semantic_bucket,
        semantic_admission_allowed,
        semantic_bucket_key,
    )

    sem_index = semantic_index
    effective_manifest = manifest_index
    if effective_manifest is None and archive.cells:
        effective_manifest = {k: c.to_json() for k, c in archive.cells.items()}
    if sem_index is None and effective_manifest is not None:
        sem_index = manifest_index_by_semantic_bucket(effective_manifest)

    bucket_key = semantic_bucket_key(room, digest)
    bucket_rows = list((sem_index or {}).get(bucket_key) or ())
    is_semantic_replace = (not is_replace) and len(bucket_rows) > 0

    if is_replace:
        if not quality_beats(quality, existing_quality):
            return None
        if not quality_replace_significant(quality, existing_quality):
            return None
        if not any(r.startswith("quality_improve:") for r in reasons):
            reasons.append(f"quality_improve:{key}")
    elif is_semantic_replace:
        if not any(r.startswith("quality_improve:") for r in reasons):
            reasons.append(f"quality_improve:bucket:{room}:{digest}")
    else:
        digest_count = _room_digest_count(
            room, manifest_index=manifest_index, archive=archive
        )
        if digest_count >= archive.max_cells_per_room:
            return None

    if not semantic_admission_allowed(
        room,
        digest,
        key,
        quality,
        manifest_index=effective_manifest,
        semantic_index=sem_index,
    ):
        return None

    last_step = int((capture_state or {}).get("last_capture_step", -10**9))
    if (not priority) and int(env_step) - last_step < capture_cooldown_steps():
        return None

    persist = canonical_store_enabled() if persist_locally is None else bool(persist_locally)

    root_cells = cells_root(project_root, archive=archive)
    go_root = go_explore_root(project_root)
    if _disk_free_bytes(go_root) < min_free_bytes():
        return None
    # Skip BizHawk save_state when the day cap is already spent (was the SPS cliff).
    if not capture_budget_available(project_root):
        return None
    if persist:
        ensure_cells_root_purged(
            root_cells,
            known_record_ids={c.record_id for c in archive.cells.values() if c.record_id},
        )
        root_cells.mkdir(parents=True, exist_ok=True)

    staging: Path | None = None
    record_id = str(existing_record_id) if existing_record_id else new_record_id()
    cell_dir = root_cells / record_id
    try:
        if persist and not acquire_slot_lock(root_cells, holder=f"go_disk:{os.getpid()}"):
            return None

        captured_at = utc_now_iso()
        staging = go_root / f".capture_staging_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        state_tmp = staging / CELL_STATE_NAME
        side_tmp = staging / CELL_SIDECAR_NAME

        save_state(state_tmp)
        if not state_tmp.is_file():
            return None
        sidecar = _dump_sidecar(
            env=env, progress=progress, state=env_state, captured_at=captured_at
        )
        side_tmp.write_text(
            json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        if not persist:
            proposal = _build_ephemeral_proposal(
                key=key,
                record_id=record_id,
                room=room,
                quality=quality,
                digest=digest,
                span=span,
                x=x,
                z=z,
                captured_at=captured_at,
                state_tmp=state_tmp,
                side_tmp=side_tmp,
                reason=reason,
                capture_reasons=reasons,
            )
            if proposal is None:
                return None
            bundle_bytes = len(base64.b64decode(str(proposal.get("bundle_b64") or "")))
            if bundle_bytes > max_capture_bundle_bytes():
                return None
            if not try_consume_capture_budget(project_root, nbytes=bundle_bytes):
                return None
            if capture_state is not None:
                capture_state["last_capture_step"] = int(env_step)
            return proposal

        cell_dir = root_cells / record_id
        meta = {
            "record_id": record_id,
            "cell_key": key,
            "room_id": room,
            "quality": list(quality),
            "milestone_digest": digest,
            "captured_at_iso": captured_at,
            "tile_span": span,
            "x": x,
            "z": z,
            "capture_reasons": list(reasons),
        }
        shas = install_cell_bundle(
            cell_dir,
            state_src=state_tmp,
            sidecar_src=side_tmp,
            meta=meta,
        )
        bundle_bytes = sum(
            p.stat().st_size
            for p in cell_dir.iterdir()
            if p.is_file()
        )
        if bundle_bytes > max_capture_bundle_bytes():
            shutil.rmtree(cell_dir, ignore_errors=True)
            return None
        if not try_consume_capture_budget(project_root, nbytes=bundle_bytes):
            shutil.rmtree(cell_dir, ignore_errors=True)
            return None
    except (OSError, RuntimeError, ValueError):
        if persist and cell_dir.is_dir() and not (cell_dir / CELL_STATE_NAME).is_file():
            shutil.rmtree(cell_dir, ignore_errors=True)
        return None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if persist:
            release_slot_lock(root_cells)

    if capture_state is not None:
        capture_state["last_capture_step"] = int(env_step)

    archive_root = archive.path.resolve().parent
    try:
        rel_bundle = (cell_dir / CELL_STATE_NAME).relative_to(archive_root).as_posix()
    except ValueError:
        rel_bundle = (cell_dir / CELL_STATE_NAME).as_posix()

    old_record_id = existing_record_id
    cell = archive.upsert(
        room_id=room,
        x=x,
        z=z,
        digest=digest,
        quality=quality,
        bundle_path=rel_bundle,
        record_id=record_id,
        meta={"captured_at_iso": captured_at, "integrity": reason},
    )
    if cell is None:
        refund_capture_budget(project_root, nbytes=bundle_bytes)
        shutil.rmtree(cell_dir, ignore_errors=True)
        return None

    if old_record_id and old_record_id != record_id:
        old_dir = root_cells / old_record_id
        if old_dir.is_dir():
            shutil.rmtree(old_dir, ignore_errors=True)

    proposal = {
        "cell_key": key,
        "record_id": record_id,
        "quality": list(quality),
        "room_id": room,
        "milestone_digest": digest,
        "paths": {
            "state": str(cell_dir / CELL_STATE_NAME),
            "sidecar": str(cell_dir / CELL_SIDECAR_NAME),
            "meta": str(cell_dir / CELL_META_NAME),
            "bundle_dir": str(cell_dir),
        },
        "state_sha256": shas["state_sha256"],
        "sidecar_sha256": shas["sidecar_sha256"],
        "captured_at_iso": captured_at,
        "bundle_path": rel_bundle,
        "capture_reasons": list(reasons),
        "meta": {"capture_reasons": list(reasons)},
    }
    return proposal


def reconcile_archive_missing_bundles(
    archive: GoExploreArchive,
    *,
    project_root: Path | str | None = None,
) -> int:
    """Drop archive rows whose on-disk cell bundle is missing (post-purge repair)."""
    cells = cells_root(project_root, archive=archive)
    removed = 0
    for key, cell in list(archive.cells.items()):
        rid = str(cell.record_id or "").strip()
        if not rid:
            del archive.cells[key]
            removed += 1
            continue
        state_path = cells / rid / CELL_STATE_NAME
        if not state_path.is_file():
            del archive.cells[key]
            removed += 1
    if removed:
        archive.save()
    return removed


def _normalize_room(room_id: str | int) -> str:
    s = str(room_id).strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    return s.upper()
