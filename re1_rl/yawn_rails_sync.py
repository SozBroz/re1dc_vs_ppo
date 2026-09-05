"""Learner-authoritative Yawn rails cell sync (fleet HTTP).

Bounded checkpoint table (``cp00``..``cpNN``), one row per ``checkpoint_index``.
Transport mirrors Go-Explore (rollout proposals → learner merge → worker poll)
but does not share ``GoExploreMerge`` semantics.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from re1_rl.go_explore_archive import quality_beats
from re1_rl.go_explore_merge import (
    CELL_META_NAME,
    CELL_POLICY_NAME,
    CELL_REPLAY_NAME,
    CELL_SIDECAR_NAME,
    CELL_STATE_NAME,
    make_cell_bundle_zip,
)

CELL_PST_NAME = "cell.pst"

DEFAULT_YAWN_RAILS_REL = "states/yawn_rails"
STORE_FILENAME = "store.json"
MANIFEST_FILENAME = "manifest.json"
_ROOT_ENV = "RE1_YAWN_RAILS_ROOT"
_SYNC_ENV = "RE1_YAWN_RAILS_SYNC"
_PREFIX_ENV = "RE1_YAWN_CELL_PREFIX"
_LOCK_NAME = "cells.sync.lock"
_STALE_LOCK_S = 180.0
# Planner-loyal seed tip (pl06; pl00 = fresh start); never prune these when
# using ``pl`` prefix.
_PLANNER_SEED_MAX_INDEX = 6


def yawn_rails_sync_enabled() -> bool:
    """Learner-authoritative cross-machine yawn sync (default on).

    ``RE1_YAWN_RAILS_SYNC=0`` disables learner merge + worker poll; local capture
    then uses compare-and-swap only on that machine.
    """
    raw = os.environ.get(_SYNC_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _planner_loyal_enabled() -> bool:
    raw = os.environ.get("RE1_PLANNER_LOYAL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def cell_dir_prefix() -> str:
    """Directory prefix for cell slots (``cp`` default; ``pl`` for planner-loyal)."""
    raw = (os.environ.get(_PREFIX_ENV) or "").strip()
    if raw:
        return raw
    if _planner_loyal_enabled():
        return "pl"
    return "cp"


def yawn_rails_rel_path() -> str:
    """Relative store root for manifest ``state_path`` strings."""
    override = os.environ.get(_ROOT_ENV, "").strip()
    if override:
        return override.replace("\\", "/").rstrip("/")
    if _planner_loyal_enabled():
        return "states/planner_loyal"
    return DEFAULT_YAWN_RAILS_REL


def _lock_path(root: Path) -> Path:
    return Path(root) / _LOCK_NAME


def _clear_stale_yawn_lock(root: Path, *, stale_s: float = _STALE_LOCK_S) -> bool:
    lp = _lock_path(root)
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


def acquire_yawn_cells_lock(
    root: Path | str,
    *,
    holder: str = "yawn_rails",
    stale_s: float = _STALE_LOCK_S,
) -> bool:
    """Exclusive lockfile under the yawn rails root. False if another holder is live."""
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    _clear_stale_yawn_lock(path, stale_s=stale_s)
    lp = _lock_path(path)
    if lp.is_file():
        return False
    payload = {
        "holder": str(holder),
        "created_unix": time.time(),
        "pid": os.getpid(),
    }
    tmp = path / f".{_LOCK_NAME}.{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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


def release_yawn_cells_lock(root: Path | str) -> None:
    try:
        _lock_path(Path(root)).unlink()
    except OSError:
        pass


def wait_for_yawn_cells_unlock(
    root: Path | str,
    *,
    timeout_s: float = 90.0,
    poll_s: float = 0.25,
    stale_s: float = _STALE_LOCK_S,
) -> bool:
    path = Path(root)
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        _clear_stale_yawn_lock(path, stale_s=stale_s)
        if not _lock_path(path).is_file():
            return True
        if time.monotonic() >= deadline:
            _clear_stale_yawn_lock(path, stale_s=stale_s)
            return not _lock_path(path).is_file()
        time.sleep(max(0.05, float(poll_s)))


@contextmanager
def yawn_cells_locked(
    root: Path | str,
    *,
    holder: str = "yawn_rails",
    timeout_s: float = 90.0,
) -> Iterator[None]:
    path = Path(root)
    if not wait_for_yawn_cells_unlock(path, timeout_s=timeout_s):
        raise TimeoutError(f"yawn cells lock timeout: {path}")
    if not acquire_yawn_cells_lock(path, holder=holder):
        if not wait_for_yawn_cells_unlock(path, timeout_s=min(5.0, timeout_s)):
            raise TimeoutError(f"yawn cells lock busy: {path}")
        if not acquire_yawn_cells_lock(path, holder=holder):
            raise TimeoutError(f"yawn cells lock acquire failed: {path}")
    try:
        yield
    finally:
        release_yawn_cells_lock(path)


def yawn_rails_root(project_root: Path | str | None = None) -> Path:
    """Absolute path to the Yawn rails cell store root."""
    override = os.environ.get(_ROOT_ENV, "").strip()
    if override:
        p = Path(override)
        if not p.is_absolute():
            base = Path(project_root) if project_root is not None else Path.cwd()
            p = base / p
        return p.resolve()
    base = Path(project_root) if project_root is not None else Path.cwd()
    rel = (
        "states/planner_loyal" if _planner_loyal_enabled() else DEFAULT_YAWN_RAILS_REL
    )
    return (base / rel).resolve()


def cell_dir_name(checkpoint_index: int) -> str:
    return f"{cell_dir_prefix()}{int(checkpoint_index):02d}"


def cell_slot_dir(root: Path | str, checkpoint_index: int) -> Path:
    return Path(root) / "cells" / cell_dir_name(checkpoint_index)


def resolve_cell_dir(root: Path | str, checkpoint_index: int) -> Path:
    """``{root}/cells/{prefix}NN`` or flat ``{root}/{prefix}NN`` (Crystals backup)."""
    nested = cell_slot_dir(root, checkpoint_index)
    if nested.is_dir():
        return nested
    flat = Path(root) / cell_dir_name(checkpoint_index)
    if flat.is_dir():
        return flat
    return nested


_SHA_CACHE: dict[str, tuple[int, int, str]] = {}


def sha256_file(path: Path | str) -> str:
    dest = Path(path)
    stat = dest.stat()
    cache_key = str(dest)
    cached = _SHA_CACHE.get(cache_key)
    mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)))
    size = int(stat.st_size)
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[2]
    digest = hashlib.sha256()
    with open(dest, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    _SHA_CACHE[cache_key] = (mtime_ns, size, hexdigest)
    return hexdigest


def slot_state_path(slot: Path | str) -> Path | None:
    """BizHawk thin cells use ``cell.State``; C-RE1 grafts use ``cell.pst``.

    Prefer ``cell.pst`` when both exist so a leftover BizHawk State cannot
    shadow the file the recomp bridge actually loads.
    """
    dest = Path(slot)
    pst = dest / CELL_PST_NAME
    if pst.is_file():
        return pst
    state = dest / CELL_STATE_NAME
    if state.is_file():
        return state
    return None


def slot_content_shas(slot: Path | str) -> tuple[str, str] | None:
    dest = Path(slot)
    state_p = slot_state_path(dest)
    side_p = dest / CELL_SIDECAR_NAME
    if state_p is None or not side_p.is_file():
        return None
    return sha256_file(state_p), sha256_file(side_p)


def slot_matches_content(
    slot: Path | str,
    *,
    state_sha256: str | None,
    sidecar_sha256: str | None = None,
) -> bool:
    """True only when on-disk State/sidecar bytes match the advertised hashes."""
    want_state = str(state_sha256 or "").strip()
    if not want_state:
        return False
    shas = slot_content_shas(slot)
    if shas is None:
        return False
    got_state, got_side = shas
    if got_state != want_state:
        return False
    want_side = str(sidecar_sha256 or "").strip()
    if want_side and got_side != want_side:
        return False
    return True


def yawn_cell_pb_bundle(chosen: dict[str, Any]) -> dict[str, Any]:
    """Reset bundle dict with file hashes so env.reset can fail closed."""
    out: dict[str, Any] = {
        "state_path": str(chosen["state_path"]),
        "sidecar_path": str(chosen["sidecar_path"]),
        "source": "yawn_rails",
    }
    for key in ("state_sha256", "sidecar_sha256"):
        val = chosen.get(key)
        if val:
            out[key] = str(val)
    return out


def _incoming_state_name(names: set[str]) -> str | None:
    """Prefer ``cell.pst`` when both payload names are present."""
    if CELL_PST_NAME in names:
        return CELL_PST_NAME
    if CELL_STATE_NAME in names:
        return CELL_STATE_NAME
    return None


def _bundle_state_name(bundle_bytes: bytes) -> str:
    try:
        names = set(zipfile.ZipFile(io.BytesIO(bundle_bytes)).namelist())
    except zipfile.BadZipFile:
        return CELL_STATE_NAME
    return _incoming_state_name(names) or CELL_STATE_NAME


def promote_cell_files(incoming: Path | str, dest: Path | str) -> None:
    """Replace live cell files in place. Never ``rmtree`` the destination dir.

    Windows cannot atomically swap a non-empty directory; deleting dest first
    leaves a missing-slot window and mixed GET /bundle reads. File ``os.replace``
    is atomic. Payload first, ``meta.json`` last.
    Accepts BizHawk ``cell.State`` or C-RE1 ``cell.pst``.
    """
    from re1_rl.win_fs_retry import replace_retry

    incoming = Path(incoming)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    incoming_names = {p.name for p in incoming.iterdir() if p.is_file()}
    if _incoming_state_name(incoming_names) is None or CELL_SIDECAR_NAME not in incoming_names:
        raise FileNotFoundError(f"incoming cell missing State/sidecar: {incoming}")
    for name in (
        CELL_STATE_NAME,
        CELL_PST_NAME,
        CELL_SIDECAR_NAME,
        CELL_REPLAY_NAME,
        CELL_POLICY_NAME,
        CELL_META_NAME,
    ):
        src = incoming / name
        if src.is_file():
            replace_retry(src, dest / name)
    for name in (CELL_REPLAY_NAME, CELL_POLICY_NAME):
        if name in incoming_names:
            continue
        stale = dest / name
        if stale.is_file():
            try:
                stale.unlink()
            except OSError:
                pass


def _existing_cell_quality(root: Path, checkpoint_index: int) -> tuple[int, ...] | None:
    """Quality for a cell listed in the store or manifest.

    Leftover ``cpNN/meta.json`` with no store/manifest row is not an incumbent.
    Those orphans used to make every later capture ``LOSE_TO_INCUMBENT`` without
    ever being sampled.
    """
    idx = int(checkpoint_index)
    listed = False
    man_p = Path(root) / MANIFEST_FILENAME
    if man_p.is_file():
        try:
            from re1_rl.win_fs_retry import read_text_retry

            man = json.loads(read_text_retry(man_p, encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            man = {}
        for row in man.get("cells") or []:
            if not isinstance(row, dict):
                continue
            try:
                if int(row["checkpoint_index"]) != idx:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            listed = True
            q = _as_quality(row.get("quality"))
            if q is not None:
                return q
            break
    store_p = Path(root) / STORE_FILENAME
    if store_p.is_file():
        try:
            raw = json.loads(store_p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        cells = raw.get("cells") or {}
        row = None
        if isinstance(cells, dict):
            row = cells.get(str(idx), cells.get(idx))
        elif isinstance(cells, list):
            for item in cells:
                if isinstance(item, dict):
                    try:
                        if int(item.get("checkpoint_index", -1)) == idx:
                            row = item
                            break
                    except (TypeError, ValueError):
                        continue
        if isinstance(row, dict):
            listed = True
            q = _as_quality(row.get("quality"))
            if q is not None:
                return q
    if not listed:
        return None
    meta_p = cell_slot_dir(root, checkpoint_index) / CELL_META_NAME
    if meta_p.is_file():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        q = _as_quality(meta.get("quality"))
        if q is not None:
            return q
    return None


def _room_dwell_frames(sidecar: dict[str, Any], room_id: str) -> int:
    """Approx dwell frames in ``room_id`` from room_entries enter steps.

    ``room_entries`` store ``(room_id, enter_step)``. Dwell for a visit is
    ``next_enter - enter``; the final open visit uses ``capture_step`` when
    present (else 0 — unknown).
    """
    room = str(room_id or "")
    if not room:
        return 0
    hist = (sidecar.get("episode_history") or {}).get("room_entries") or []
    entries: list[tuple[str, int]] = []
    for entry in hist:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        try:
            entries.append((str(entry[0]), int(entry[1] or 0)))
        except (TypeError, ValueError):
            continue
    try:
        capture_step = int(sidecar.get("capture_step") or 0)
    except (TypeError, ValueError):
        capture_step = 0
    total = 0
    for i, (rid, step) in enumerate(entries):
        if rid != room:
            continue
        if i + 1 < len(entries):
            end = entries[i + 1][1]
        elif capture_step > step:
            end = capture_step
        else:
            continue
        if end > step:
            total += end - step
    return total


def try_install_yawn_cell(
    project_root: Path | str,
    *,
    checkpoint_index: int,
    staged_dir: Path,
    quality: list[int] | tuple[int, ...],
    row: dict[str, Any],
    holder: str = "yawn_capture",
    force: bool = False,
) -> bool:
    """Compare-and-swap install from ``staged_dir`` into curated ``cpNN``.

    Pay-forward model: completing checkpoint *N* proposes cell cpNN; install
    when new quality strictly beats the incumbent (``quality_beats`` +
    ``quality_replace_significant``). Cutscene keys are not compared.
    ``force=True`` skips the quality gate (operator pin / restore).
    Returns True when the curated slot was updated.
    """
    from re1_rl.go_explore_capture import quality_replace_significant

    root = yawn_rails_root(project_root)
    idx = int(checkpoint_index)
    new_q = _as_quality(quality)
    if new_q is None:
        return False
    from re1_rl.go_explore_archive import (
        LEG_FRAMES_QUALITY_INDEX,
        LEG_FRAMES_SENTINEL,
    )

    if int(new_q[LEG_FRAMES_QUALITY_INDEX]) == -int(LEG_FRAMES_SENTINEL):
        print(
            f"[yawn_install] reject sentinel_leg_frames cp{idx:02d}",
            flush=True,
        )
        return False
    if idx > 0:
        pred = cell_slot_dir(root, idx - 1)
        if slot_state_path(pred) is None:
            print(
                f"[yawn_install] reject missing_predecessor "
                f"cp{idx:02d} need=cp{idx - 1:02d}",
                flush=True,
            )
            return False
    state_src = slot_state_path(staged_dir)
    side_src = Path(staged_dir) / CELL_SIDECAR_NAME
    if state_src is None or not side_src.is_file():
        return False
    try:
        new_side = json.loads(side_src.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False

    with yawn_cells_locked(root, holder=holder):
        dest = cell_slot_dir(root, idx)
        if not force:
            old_q = _existing_cell_quality(root, idx)
            if old_q is not None:
                if not quality_beats(new_q, old_q):
                    return False
                if not quality_replace_significant(new_q, old_q):
                    return False

        incoming = dest.parent / f".incoming_{cell_dir_name(idx)}_{os.getpid()}"
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(state_src, incoming / state_src.name)
            shutil.copy2(side_src, incoming / CELL_SIDECAR_NAME)
            replay_src = Path(staged_dir) / CELL_REPLAY_NAME
            if replay_src.is_file():
                shutil.copy2(replay_src, incoming / CELL_REPLAY_NAME)
            policy_src = Path(staged_dir) / CELL_POLICY_NAME
            if policy_src.is_file():
                shutil.copy2(policy_src, incoming / CELL_POLICY_NAME)
            shas = slot_content_shas(incoming)
            if shas is None:
                shutil.rmtree(incoming, ignore_errors=True)
                return False
            state_sha, side_sha = shas
            meta_src = Path(staged_dir) / CELL_META_NAME
            if meta_src.is_file():
                try:
                    meta_obj = json.loads(meta_src.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    meta_obj = dict(row)
            else:
                meta_obj = dict(row)
            if not isinstance(meta_obj, dict):
                meta_obj = dict(row)
            meta_obj["checkpoint_index"] = idx
            meta_obj["quality"] = list(new_q)
            meta_obj["state_sha256"] = state_sha
            meta_obj["sidecar_sha256"] = side_sha
            (incoming / CELL_META_NAME).write_text(
                json.dumps(meta_obj, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            promote_cell_files(incoming, dest)
        except OSError:
            shutil.rmtree(incoming, ignore_errors=True)
            return False
        shutil.rmtree(incoming, ignore_errors=True)

        install_row = dict(row)
        install_row["checkpoint_index"] = idx
        install_row["quality"] = list(new_q)
        install_row["state_sha256"] = state_sha
        install_row["sidecar_sha256"] = side_sha
        rel = yawn_rails_rel_path()
        install_row.setdefault(
            "state_path",
            f"{rel}/cells/{cell_dir_name(idx)}/{state_src.name}",
        )
        install_row.setdefault(
            "sidecar_path",
            f"{rel}/cells/{cell_dir_name(idx)}/{CELL_SIDECAR_NAME}",
        )
        store = YawnRailsCellStore(root)
        store.cells[idx] = dict(install_row)
        if install_row.get("route_id"):
            store.route_id = str(install_row["route_id"])
        store.archive_version = int(store.archive_version or 0) + 1
        store._persist_unlocked()
        try:
            from re1_rl.yawn_rails_payforward import notify_payforward_install

            notify_payforward_install(
                Path(project_root),
                installed_index=idx,
                cells=[
                    dict(r, checkpoint_index=i)
                    for i, r in store.cells.items()
                    if int(i) >= 18
                ],
            )
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return True


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _as_quality(raw: Any) -> tuple[int, ...] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 5:
        return None
    try:
        from re1_rl.go_explore_archive import normalize_quality

        return normalize_quality(raw)
    except (TypeError, ValueError):
        return None


def extract_yawn_rails_proposals(
    infos: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Pull ``yawn_rails_capture`` lists/dicts out of rollout episode infos."""
    out: list[dict[str, Any]] = []
    for info in infos or []:
        if not isinstance(info, dict):
            continue
        caps = info.get("yawn_rails_capture")
        if caps is None:
            continue
        if isinstance(caps, dict):
            out.append(caps)
        elif isinstance(caps, list):
            for row in caps:
                if isinstance(row, dict):
                    out.append(row)
    return out


def pack_cell_bundle(
    *,
    state_bytes: bytes,
    sidecar: dict[str, Any] | bytes | str,
    meta: dict[str, Any] | None = None,
    replay: dict[str, Any] | bytes | str | None = None,
    policy: bytes | None = None,
    state_name: str = CELL_STATE_NAME,
) -> bytes:
    """Zip ``cell.State`` or ``cell.pst`` + sidecar (+ optional meta / replay / policy)."""
    if isinstance(sidecar, (bytes, bytearray)):
        side_obj = json.loads(bytes(sidecar).decode("utf-8"))
    elif isinstance(sidecar, str):
        side_obj = json.loads(sidecar)
    else:
        side_obj = dict(sidecar)
    name = str(state_name or CELL_STATE_NAME)
    if name not in {CELL_STATE_NAME, CELL_PST_NAME}:
        name = CELL_STATE_NAME
    if name == CELL_STATE_NAME:
        return make_cell_bundle_zip(
            state_bytes=state_bytes,
            sidecar=side_obj,
            meta=meta,
            replay=replay,
            policy=policy,
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, state_bytes)
        zf.writestr(
            CELL_SIDECAR_NAME,
            json.dumps(side_obj, indent=2, sort_keys=True) + "\n",
        )
        if meta is not None:
            zf.writestr(
                CELL_META_NAME,
                json.dumps(meta, indent=2, sort_keys=True) + "\n",
            )
        if replay is not None:
            if isinstance(replay, (bytes, bytearray)):
                replay_text = bytes(replay).decode("utf-8")
            elif isinstance(replay, str):
                replay_text = replay
            else:
                replay_text = json.dumps(replay, separators=(",", ":")) + "\n"
            if not replay_text.endswith("\n"):
                replay_text += "\n"
            zf.writestr(CELL_REPLAY_NAME, replay_text)
        if policy:
            zf.writestr(CELL_POLICY_NAME, bytes(policy))
    return buf.getvalue()


def build_capture_proposal(
    *,
    route_id: str,
    checkpoint_index: int,
    checkpoint_id: str,
    room_id: str,
    quality: tuple[int, ...] | list[int],
    state_path: Path,
    sidecar_path: Path,
    worker_id: str | None = None,
    capacity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pack a local cell capture into a rollout proposal dict."""
    state_bytes = Path(state_path).read_bytes()
    side_text = Path(sidecar_path).read_text(encoding="utf-8")
    sidecar = json.loads(side_text)
    replay = None
    replay_path = Path(state_path).parent / CELL_REPLAY_NAME
    if replay_path.is_file():
        try:
            replay = json.loads(replay_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            replay = None
    policy = None
    policy_path = Path(state_path).parent / CELL_POLICY_NAME
    if policy_path.is_file():
        try:
            policy = policy_path.read_bytes()
        except OSError:
            policy = None
    from re1_rl.go_explore_archive import normalize_quality

    q = list(normalize_quality(quality))
    while len(q) < 5:
        q.append(0)
    meta = {
        "route_id": str(route_id),
        "checkpoint_index": int(checkpoint_index),
        "checkpoint_id": str(checkpoint_id),
        "room_id": str(room_id),
        "quality": q,
    }
    if worker_id:
        meta["worker_id"] = str(worker_id)
    capacity_meta = {
        key: (capacity or {}).get(key)
        for key in (
            "inventory_free_slots",
            "next_checkpoint_id",
            "next_slots_needed",
            "inventory_feasible",
            "captured_in_box_room",
        )
        if key in (capacity or {})
    }
    meta.update(capacity_meta)
    state_name = Path(state_path).name
    if state_name not in {CELL_STATE_NAME, CELL_PST_NAME}:
        state_name = CELL_STATE_NAME
    blob = pack_cell_bundle(
        state_bytes=state_bytes,
        sidecar=sidecar,
        meta=meta,
        replay=replay,
        policy=policy,
        state_name=state_name,
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        state_sha = _sha256_bytes(zf.read(state_name))
        side_sha = _sha256_bytes(zf.read(CELL_SIDECAR_NAME))
    return {
        "route_id": str(route_id),
        "checkpoint_index": int(checkpoint_index),
        "checkpoint_id": str(checkpoint_id),
        "room_id": str(room_id),
        "quality": q,
        "bundle_b64": base64.b64encode(blob).decode("ascii"),
        "bundle_sha256": _sha256_bytes(blob),
        "state_sha256": state_sha,
        "sidecar_sha256": side_sha,
        "bytes": len(blob),
        "worker_id": worker_id,
        "meta": meta,
        **capacity_meta,
    }


class YawnRailsCellStore:
    """Canonical learner store: one bundle per checkpoint index."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else yawn_rails_root()
        self.archive_version = 0
        self.route_id: str | None = None
        self.cells: dict[int, dict[str, Any]] = {}
        self.accepted = 0
        self.rejected = 0
        self.last_rejects: list[str] = []
        self._lock = threading.Lock()
        self._load()

    @property
    def store_path(self) -> Path:
        return self.root / STORE_FILENAME

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    def _load(self) -> None:
        path = self.store_path
        if not path.is_file():
            # Bootstrap from curriculum-style manifest if present.
            self._load_manifest_fallback()
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            self._load_manifest_fallback()
            return
        self.archive_version = int(raw.get("archive_version", 0) or 0)
        self.route_id = str(raw.get("route_id") or "") or None
        cells: dict[int, dict[str, Any]] = {}
        raw_cells = raw.get("cells") or {}
        # Canonical store uses dict keyed by index; wipe/seed scripts may leave
        # a curriculum-style list (same shape as manifest.json).
        if isinstance(raw_cells, list):
            pairs: list[tuple[Any, Any]] = [
                (row.get("checkpoint_index", i), row)
                for i, row in enumerate(raw_cells)
                if isinstance(row, dict)
            ]
        elif isinstance(raw_cells, dict):
            pairs = list(raw_cells.items())
        else:
            pairs = []
        for key, row in pairs:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row.get("checkpoint_index", key))
            except (TypeError, ValueError):
                continue
            cells[idx] = dict(row)
            cells[idx]["checkpoint_index"] = idx
        self.cells = cells

    def _load_manifest_fallback(self) -> None:
        path = self.manifest_path
        if not path.is_file():
            self.archive_version = 0
            self.cells = {}
            return
        try:
            from re1_rl.win_fs_retry import read_text_retry

            raw = json.loads(read_text_retry(path, encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            self.archive_version = 0
            self.cells = {}
            return
        self.archive_version = int(raw.get("archive_version", 0) or 0)
        self.route_id = str(raw.get("route_id") or "") or None
        cells: dict[int, dict[str, Any]] = {}
        for row in raw.get("cells") or []:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row["checkpoint_index"])
            except (KeyError, TypeError, ValueError):
                continue
            cells[idx] = dict(row)
        self.cells = cells

    def _persist_unlocked(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "archive_version": int(self.archive_version),
            "route_id": self.route_id,
            "cells": {
                str(idx): self.cells[idx]
                for idx in sorted(self.cells)
            },
        }
        fd, tmp_name = tempfile.mkstemp(
            prefix="yawn_rails_store_",
            suffix=".json",
            dir=str(self.root),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_name, self.store_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self._write_sampling_manifest_unlocked()

    def _write_sampling_manifest_unlocked(self) -> None:
        """Curriculum-facing manifest consumed by ``sample_one_leg_options``."""
        cells = []
        rel = yawn_rails_rel_path()
        for idx in sorted(self.cells):
            row = dict(self.cells[idx])
            payload = slot_state_path(cell_slot_dir(self.root, idx))
            payload_name = payload.name if payload is not None else CELL_STATE_NAME
            row["state_path"] = (
                f"{rel}/cells/{cell_dir_name(idx)}/{payload_name}"
            )
            row["sidecar_path"] = (
                f"{rel}/cells/{cell_dir_name(idx)}/{CELL_SIDECAR_NAME}"
            )
            cells.append(row)
        manifest = {
            "schema_version": 1,
            "route_id": self.route_id,
            "archive_version": int(self.archive_version),
            "cells": cells,
        }
        tmp = self.manifest_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        from re1_rl.win_fs_retry import replace_retry

        replace_retry(tmp, self.manifest_path)

    def _backfill_file_hashes_unlocked(self) -> bool:
        """Fill missing state/sidecar hashes from files already on disk."""
        changed = False
        for idx, row in self.cells.items():
            if not isinstance(row, dict):
                continue
            if str(row.get("state_sha256") or "").strip() and str(
                row.get("sidecar_sha256") or ""
            ).strip():
                continue
            shas = slot_content_shas(cell_slot_dir(self.root, idx))
            if shas is None:
                continue
            row["state_sha256"], row["sidecar_sha256"] = shas
            changed = True
        return changed

    def ingest_proposals(self, proposals: list[dict[str, Any]]) -> list[str]:
        """Admit/replace cells. Returns accepted ``cpNN`` ids."""
        accepted: list[str] = []
        self.last_rejects = []
        if not proposals:
            return accepted
        with self._lock:
            with yawn_cells_locked(self.root, holder="yawn_learner_ingest"):
                self._load()
                hashed = self._backfill_file_hashes_unlocked()
                for prop in proposals:
                    cid = self._ingest_one_unlocked(prop)
                    if cid is not None:
                        accepted.append(cid)
                if accepted or hashed:
                    self.archive_version += 1
                    self._persist_unlocked()
        if accepted:
            try:
                from re1_rl.yawn_rails_payforward import notify_payforward_install

                # self.root is states/yawn_rails → project root is parents[1].
                project = (
                    self.root.parent.parent
                    if self.root.name == "yawn_rails"
                    else Path(self.root)
                )
                cells = []
                for idx, row in self.cells.items():
                    if int(idx) < 18:
                        continue
                    entry = dict(row)
                    entry["checkpoint_index"] = int(idx)
                    cells.append(entry)
                prefix = cell_dir_prefix()
                for cid in accepted:
                    raw = str(cid)
                    try:
                        idx = (
                            int(raw[len(prefix) :], 10)
                            if raw.startswith(prefix)
                            else int(raw.replace("cp", "").replace("pl", ""), 10)
                        )
                    except ValueError:
                        continue
                    notify_payforward_install(
                        project, installed_index=idx, cells=cells
                    )
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
        return accepted

    def _reject(self, reason: str) -> None:
        self.rejected += 1
        self.last_rejects.append(reason)

    def _ingest_one_unlocked(self, prop: dict[str, Any]) -> str | None:
        try:
            idx = int(prop["checkpoint_index"])
        except (KeyError, TypeError, ValueError):
            self._reject("bad_checkpoint_index")
            return None
        if idx < 0:
            self._reject(f"idx={idx}: negative")
            return None
        quality = _as_quality(prop.get("quality"))
        if quality is None:
            self._reject(f"idx={idx}: bad_quality")
            return None
        route_id = str(prop.get("route_id") or "") or None
        if self.route_id and route_id and route_id != self.route_id:
            self._reject(
                f"idx={idx}: route_mismatch store={self.route_id} got={route_id}"
            )
            return None
        if route_id and not self.route_id:
            self.route_id = route_id
        if prop.get("inventory_feasible") is False:
            self._reject(f"idx={idx}: inventory_infeasible")
            return None

        existing = self.cells.get(idx)
        capacity_upgrade = False
        if existing is not None:
            old_q = _as_quality(existing.get("quality"))
            capacity_upgrade = (
                "inventory_feasible" not in existing
                and prop.get("inventory_feasible") is True
            )
            if (
                not capacity_upgrade
                and old_q is not None
                and not quality_beats(quality, old_q)
            ):
                self._reject(f"idx={idx}: quality_does_not_beat")
                return None
            from re1_rl.go_explore_capture import quality_replace_significant

            if (
                not capacity_upgrade
                and old_q is not None
                and not quality_replace_significant(quality, old_q)
            ):
                self._reject(f"idx={idx}: quality_not_significant")
                return None

        bundle_bytes = self._decode_bundle(prop)
        if bundle_bytes is None:
            self._reject(f"idx={idx}: missing_bundle")
            return None
        ok, reason, state_sha, side_sha = self._validate_bundle_bytes(
            bundle_bytes, prop
        )
        if not ok or not state_sha or not side_sha:
            self._reject(f"idx={idx}: {reason or 'bad_bundle'}")
            return None

        bundle_sha = _sha256_bytes(bundle_bytes)
        self._write_bundle_unlocked(
            idx, bundle_bytes, prop, bundle_sha, state_sha, side_sha
        )
        row = {
            "checkpoint_index": idx,
            "checkpoint_id": str(prop.get("checkpoint_id") or ""),
            "room_id": str(prop.get("room_id") or ""),
            "quality": list(quality),
            "bundle_sha256": bundle_sha,
            "state_sha256": state_sha,
            "sidecar_sha256": side_sha,
            "bytes": len(bundle_bytes),
            "state_path": (
                f"{yawn_rails_rel_path()}/cells/{cell_dir_name(idx)}/"
                f"{_bundle_state_name(bundle_bytes)}"
            ),
            "sidecar_path": (
                f"{yawn_rails_rel_path()}/cells/{cell_dir_name(idx)}/{CELL_SIDECAR_NAME}"
            ),
        }
        for key in (
            "inventory_free_slots",
            "next_checkpoint_id",
            "next_slots_needed",
            "inventory_feasible",
            "captured_in_box_room",
            "training_start",
            "chunk_final",
            "chunk_id",
            "planner_step_index",
            "source",
        ):
            if key in prop:
                row[key] = prop[key]
        if prop.get("worker_id"):
            row["worker_id"] = str(prop["worker_id"])
        self.cells[idx] = row
        self.accepted += 1
        return cell_dir_name(idx)

    def _decode_bundle(self, prop: dict[str, Any]) -> bytes | None:
        b64 = prop.get("bundle_b64")
        if not b64:
            return None
        try:
            return base64.b64decode(str(b64), validate=False)
        except (ValueError, TypeError):
            return None

    def _validate_bundle_bytes(
        self, data: bytes, prop: dict[str, Any]
    ) -> tuple[bool, str, str | None, str | None]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return False, "bad_zip", None, None
        names = set(zf.namelist())
        state_name = _incoming_state_name(names)
        if state_name is None:
            return False, "missing_state", None, None
        if CELL_SIDECAR_NAME not in names:
            return False, "missing_sidecar", None, None
        state_bytes = zf.read(state_name)
        side_bytes = zf.read(CELL_SIDECAR_NAME)
        state_sha = _sha256_bytes(state_bytes)
        side_sha = _sha256_bytes(side_bytes)
        want_state = str(prop.get("state_sha256") or "").strip()
        if want_state and state_sha != want_state:
            return False, "state_sha_mismatch", None, None
        want_side = str(prop.get("sidecar_sha256") or "").strip()
        if want_side and side_sha != want_side:
            return False, "sidecar_sha_mismatch", None, None
        return True, "ok", state_sha, side_sha

    def _write_bundle_unlocked(
        self,
        checkpoint_index: int,
        bundle_bytes: bytes,
        prop: dict[str, Any],
        bundle_sha: str,
        state_sha: str,
        side_sha: str,
    ) -> None:
        dest = cell_slot_dir(self.root, checkpoint_index)
        dest.parent.mkdir(parents=True, exist_ok=True)
        incoming = dest.parent / (
            f".incoming_{cell_dir_name(checkpoint_index)}_{os.getpid()}"
        )
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
                zf.extractall(incoming)
            meta = {
                "checkpoint_index": int(checkpoint_index),
                "checkpoint_id": prop.get("checkpoint_id"),
                "room_id": prop.get("room_id"),
                "quality": list(prop.get("quality") or []),
                "bundle_sha256": bundle_sha,
                "state_sha256": state_sha,
                "sidecar_sha256": side_sha,
                "bytes": len(bundle_bytes),
                "route_id": prop.get("route_id") or self.route_id,
            }
            for key in (
                "inventory_free_slots",
                "next_checkpoint_id",
                "next_slots_needed",
                "inventory_feasible",
                "captured_in_box_room",
                "training_start",
                "chunk_final",
                "chunk_id",
                "planner_step_index",
                "planner_step",
                "source",
            ):
                if key in prop:
                    meta[key] = prop[key]
            (incoming / CELL_META_NAME).write_text(
                json.dumps(meta, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            promote_cell_files(incoming, dest)
        except Exception:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
        shutil.rmtree(incoming, ignore_errors=True)

    def build_manifest(self, *, since_version: int = 0) -> dict[str, Any]:
        with self._lock:
            self._load()
            if self._backfill_file_hashes_unlocked():
                self.archive_version = int(self.archive_version or 0) + 1
                self._persist_unlocked()
            ver = int(self.archive_version)
            cell_count = len(self.cells)
            # Version 0 is "bootstrapped, never ingested". Clients polling
            # since_version=0 must still receive the snapshot (yawn did this
            # after the first admit bumped the version). `>=` on ver=0 hid
            # every cell and workers never pulled.
            if int(since_version) > 0 and int(since_version) >= ver:
                return {
                    "archive_version": ver,
                    "route_id": self.route_id,
                    "cells": [],
                    "cell_count": cell_count,
                }
            cells_out = []
            for idx in sorted(self.cells):
                row = self.cells[idx]
                cells_out.append(
                    {
                        "checkpoint_index": idx,
                        "checkpoint_id": row.get("checkpoint_id", ""),
                        "room_id": row.get("room_id", ""),
                        "quality": list(row.get("quality") or []),
                        "bundle_sha256": str(row.get("bundle_sha256") or ""),
                        "state_sha256": str(row.get("state_sha256") or ""),
                        "sidecar_sha256": str(row.get("sidecar_sha256") or ""),
                        "bytes": int(row.get("bytes") or 0),
                        "cell_id": cell_dir_name(idx),
                        "state_path": row.get("state_path"),
                        "sidecar_path": row.get("sidecar_path"),
                        **{
                            key: row[key]
                            for key in (
                                "inventory_free_slots",
                                "next_checkpoint_id",
                                "next_slots_needed",
                                "inventory_feasible",
                                "captured_in_box_room",
                                "training_start",
                                "chunk_final",
                                "chunk_id",
                                "planner_step_index",
                            )
                            if key in row
                        },
                    }
                )
            return {
                "archive_version": ver,
                "route_id": self.route_id,
                "cells": cells_out,
                "cell_count": len(cells_out),
            }

    def pack_bundle_zip(self, cell_id: str) -> bytes | None:
        """Zip bytes for ``GET /yawn_rails/bundle/<prefixNN>``."""
        cid = str(cell_id).strip()
        if "/" in cid or "\\" in cid or ".." in cid:
            return None
        idx = None
        for prefix in (cell_dir_prefix(), "pl", "cp"):
            if cid.startswith(prefix):
                try:
                    idx = int(cid[len(prefix) :], 10)
                    break
                except ValueError:
                    continue
        if idx is None:
            return None
        with yawn_cells_locked(self.root, holder="yawn_pack_bundle"):
            d = cell_slot_dir(self.root, idx)
            state_p = slot_state_path(d)
            if state_p is None:
                for alt in ("pl", "cp"):
                    cand = Path(self.root) / "cells" / f"{alt}{int(idx):02d}"
                    state_p = slot_state_path(cand)
                    if state_p is not None:
                        d = cand
                        break
            side_p = d / CELL_SIDECAR_NAME
            if state_p is None or not side_p.is_file():
                return None
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(state_p, state_p.name)
                zf.write(side_p, CELL_SIDECAR_NAME)
                meta_p = d / CELL_META_NAME
                if meta_p.is_file():
                    zf.write(meta_p, CELL_META_NAME)
                replay_p = d / CELL_REPLAY_NAME
                if replay_p.is_file():
                    zf.write(replay_p, CELL_REPLAY_NAME)
                policy_p = d / CELL_POLICY_NAME
                if policy_p.is_file():
                    zf.write(policy_p, CELL_POLICY_NAME)
            return buf.getvalue()


def yawn_rails_store_from_env(
    project_root: Path | str | None = None,
) -> YawnRailsCellStore:
    """Always-on store under ``states/yawn_rails`` (or ``RE1_YAWN_RAILS_ROOT``)."""
    return YawnRailsCellStore(yawn_rails_root(project_root))
