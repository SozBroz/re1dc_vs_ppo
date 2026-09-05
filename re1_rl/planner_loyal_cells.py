"""Planner-loyal cell archive under ``states/planner_loyal``.

Slot numbering (C-RE1 / recomp era): ``pl00`` is the dining-105 fresh
start (no emblem; ``planner_step_index`` -1, seek 0). The opening chunk
(``opening_to_lockpick``) mints ``pl01`` emblem .. ``pl06`` Barry lockpick
via explicit ``slot_index``. The live chunk (``cp05_shield_key``) starts
from the ``pl06`` tip and its completed step 0 (``106->105``) mints ``pl07``.
Legacy BizHawk ``cell.State`` cells in the same tree keep their old
numbering (``pl05`` tip, ``pl06`` = step 0); their ``planner_step_index``
meta still seeks the queue correctly.

Training starts: every loadable cell at the tip (``pl06``) and later that
still has a remaining planner step in the live chunk, plus any cell whose
meta says ``training_start: true`` (``pl00``) or whose slot is pinned.
The cell that completed the last authored step is not a start (episode
already ended there). Synced cells missing the ``training_start`` flag
still count — the slot index is enough.

Optional hot-reload pin (read every reset, no worker restart after the
code is loaded): ``data/planner_loyal_reset_pin.env`` or
``RE1_PLANNER_RESET_PIN_FILE``. Blank knobs keep the full ``pl06+``
pool. ``RE1_PLANNER_RESET_PIN_INDEX`` wins over
``RE1_PLANNER_RESET_PIN_RANGE`` over ``RE1_PLANNER_RESET_PIN_WEIGHTS``
over ``RE1_PLANNER_RESET_PIN_SET``. Exclusive pins that match no minted
start fall back to the unpinned pool.
``SET`` plus ``SET_WEIGHT=0.5`` (or ``WEIGHTS=54:50,rest:50``) puts that
fraction on the named cells and the remainder on the other minted starts.

Captures are always thin: ``cell.State`` + sidecar + ``meta.json`` with a
quality vector for healthier-thin comparison. No ``leg_replay`` / ``leg_policy``.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
from pathlib import Path
from typing import Any

from re1_rl.go_explore_merge import (
    CELL_META_NAME,
    CELL_SIDECAR_NAME,
    CELL_STATE_NAME,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLANNER_LOYAL_REL = "states/planner_loyal"
CRYSTALS_REL = "backups/Crystals_in_time"
_ROOT_ENV = "RE1_PLANNER_LOYAL_CELLS_ROOT"
_RECOMP_CELLS_ENV = "RE1_RECOMP_CELLS"
_RECOMP_STATE_NAME = "cell.pst"

# Crystals cp00..cp05 → planner-loyal pl01..pl06 (pl00 = fresh start)
SEED_SOURCE_MAX = 5  # inclusive; barry_hall_return_106
TRAINING_START_INDEX = 6  # earliest live-chunk start (lockpick tip)
SEED_SLOT_OFFSET = TRAINING_START_INDEX - SEED_SOURCE_MAX  # cpNN → pl(NN+1)
FRESH_START_INDEX = 0  # dining-105 fresh start, planner_step_index -1

_PIN_INDEX_ENV = "RE1_PLANNER_RESET_PIN_INDEX"
_PIN_RANGE_ENV = "RE1_PLANNER_RESET_PIN_RANGE"
_PIN_SET_ENV = "RE1_PLANNER_RESET_PIN_SET"
_PIN_SET_WEIGHT_ENV = "RE1_PLANNER_RESET_PIN_SET_WEIGHT"
_PIN_WEIGHTS_ENV = "RE1_PLANNER_RESET_PIN_WEIGHTS"
_PIN_FILE_ENV = "RE1_PLANNER_RESET_PIN_FILE"
_DEFAULT_PIN_FILE = "data/planner_loyal_reset_pin.env"
_PIN_REST_KEYS = frozenset({"rest", "uniform", "pool"})

MANIFEST_FILENAME = "manifest.json"
ROUTE_ID = "planner_loyal_v1"
_THIN_ONLY_NAMES = (CELL_STATE_NAME, CELL_SIDECAR_NAME, CELL_META_NAME)


def planner_loyal_root(project_root: Path | str | None = None) -> Path:
    raw = (os.environ.get(_ROOT_ENV) or "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else (Path(project_root or ROOT) / path)
    return Path(project_root or ROOT) / DEFAULT_PLANNER_LOYAL_REL


def recomp_cells_enabled() -> bool:
    return os.environ.get(_RECOMP_CELLS_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cell_state_filename() -> str:
    """BizHawk thin cells use cell.State; C-RE1 grafts use cell.pst."""
    return _RECOMP_STATE_NAME if recomp_cells_enabled() else CELL_STATE_NAME


def _slot_payload_path(slot: Path) -> Path | None:
    """Prefer this runtime's payload; keep the other so rewrite_manifest
    does not drop BizHawk ``cell.State`` cells when a C-RE1 worker scans."""
    preferred = slot / cell_state_filename()
    if preferred.is_file():
        return preferred
    other_name = (
        CELL_STATE_NAME if cell_state_filename() == _RECOMP_STATE_NAME else _RECOMP_STATE_NAME
    )
    other = slot / other_name
    return other if other.is_file() else None


def cell_dir_name(index: int) -> str:
    return f"pl{int(index):02d}"


def cell_slot_dir(root: Path | str, index: int) -> Path:
    return Path(root) / "cells" / cell_dir_name(index)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def slot_index_for_completed_step(
    completed_step_index: int,
    steps: list[dict[str, Any]] | None = None,
) -> int:
    """Map a completed planner-queue step to the next plNN slot.

    Steps with ``capture: false`` do not consume a slot (Richard bleedout), so
    the following capturing hop after ``pl83`` is ``pl84`` (``204->207``).
    """
    idx = int(completed_step_index)
    if steps and 0 <= idx < len(steps):
        raw = steps[idx].get("slot_index") if isinstance(steps[idx], dict) else None
        if raw is not None:
            return int(raw)
    if not steps:
        return int(TRAINING_START_INDEX) + 1 + idx
    cap = 0
    for i, step in enumerate(steps):
        if isinstance(step, dict) and step.get("capture") is False:
            if i == idx:
                # Caller should not mint these; keep a deterministic fallback.
                return int(TRAINING_START_INDEX) + 1 + cap
            continue
        if i == idx:
            return int(TRAINING_START_INDEX) + 1 + cap
        cap += 1
    return int(TRAINING_START_INDEX) + 1 + idx


def _strip_fat_artifacts(cell_dir: Path) -> None:
    for name in ("leg_replay.json", "leg_policy.npz"):
        path = cell_dir / name
        if path.is_file():
            path.unlink()


def bootstrap_from_crystals(
    project_root: Path | str | None = None,
    *,
    crystals_root: Path | str | None = None,
    through_index: int = SEED_SOURCE_MAX,
    force: bool = False,
) -> dict[str, Any]:
    """Copy Crystals ``cp00``..``cp{through}`` into thin ``states/planner_loyal`` cells.

    ``cpNN`` lands in ``pl(NN + SEED_SLOT_OFFSET)`` so the lockpick tip is
    ``pl06``. Existing slots are left untouched unless ``force``.
    """
    root = Path(project_root or ROOT)
    crystals = Path(crystals_root) if crystals_root else root / CRYSTALS_REL
    dest_root = planner_loyal_root(root)
    cells_dir = dest_root / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    for src_i in range(0, int(through_index) + 1):
        src = crystals / f"cp{src_i:02d}"
        if not (src / CELL_STATE_NAME).is_file():
            raise FileNotFoundError(f"missing Crystals cell: {src / CELL_STATE_NAME}")
        slot_i = src_i + SEED_SLOT_OFFSET
        dst = cell_slot_dir(dest_root, slot_i)
        if dst.exists() and not force:
            # Never rewrite a live slot's meta (it may hold a minted cell).
            continue
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True, exist_ok=True)
        for name in _THIN_ONLY_NAMES:
            src_f = src / name
            if src_f.is_file():
                shutil.copy2(src_f, dst / name)
        meta = json.loads((dst / CELL_META_NAME).read_text(encoding="utf-8"))

        _strip_fat_artifacts(dst)
        meta["route_id"] = ROUTE_ID
        meta["checkpoint_index"] = slot_i
        meta["source"] = {
            "archive": "Crystals_in_time",
            "checkpoint": f"cp{src_i:02d}",
        }
        # Tip pl06 is the earliest live start; earlier seeds stay archive only.
        meta["training_start"] = slot_i == TRAINING_START_INDEX
        state_p = dst / CELL_STATE_NAME
        sidecar_p = dst / CELL_SIDECAR_NAME
        if state_p.is_file():
            meta["state_sha256"] = _sha256_file(state_p)
            meta["bytes"] = state_p.stat().st_size
        if sidecar_p.is_file():
            meta["sidecar_sha256"] = _sha256_file(sidecar_p)
        (dst / CELL_META_NAME).write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        copied.append(
            {
                "checkpoint_index": slot_i,
                "checkpoint_id": meta.get("checkpoint_id"),
                "room_id": meta.get("room_id"),
                "training_start": bool(meta.get("training_start")),
                "state_path": f"{DEFAULT_PLANNER_LOYAL_REL}/cells/{cell_dir_name(slot_i)}/{CELL_STATE_NAME}",
                "sidecar_path": f"{DEFAULT_PLANNER_LOYAL_REL}/cells/{cell_dir_name(slot_i)}/{CELL_SIDECAR_NAME}",
            }
        )

    manifest = {
        "schema_version": 1,
        "route_id": ROUTE_ID,
        "training_start_index": TRAINING_START_INDEX,
        "seed_source": str(CRYSTALS_REL),
        "seed_through": through_index,
        "cells": _scan_cells(dest_root),
    }
    (dest_root / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "root": str(dest_root),
        "training_start_index": TRAINING_START_INDEX,
        "copied": copied,
        "n_manifest_cells": len(manifest["cells"]),
    }


def _scan_cells(dest_root: Path) -> list[dict[str, Any]]:
    cells_dir = dest_root / "cells"
    rows: list[dict[str, Any]] = []
    if not cells_dir.is_dir():
        return rows
    for path in sorted(cells_dir.iterdir()):
        if not path.is_dir() or not path.name.startswith("pl"):
            continue
        try:
            idx = int(path.name[2:])
        except ValueError:
            continue
        meta_p = path / CELL_META_NAME
        state_p = _slot_payload_path(path)
        sidecar_p = path / CELL_SIDECAR_NAME
        if state_p is None or not sidecar_p.is_file():
            continue
        meta: dict[str, Any] = {}
        if meta_p.is_file():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        # Prefer absolute paths when cells root is overridden (recomp grafts).
        root = dest_root.resolve()
        state_rel = str(state_p.resolve())
        side_rel = str(sidecar_p.resolve())
        try:
            state_rel = str(state_p.resolve().relative_to(root.parent.parent))
            side_rel = str(sidecar_p.resolve().relative_to(root.parent.parent))
        except ValueError:
            pass
        if recomp_cells_enabled():
            state_rel = str(state_p.resolve())
            side_rel = str(sidecar_p.resolve())
        rows.append(
            {
                "checkpoint_index": idx,
                "checkpoint_id": meta.get("checkpoint_id") or path.name,
                "room_id": meta.get("room_id"),
                "quality": meta.get("quality") or [],
                # Slot >= tip counts even when synced meta dropped the flag;
                # an explicit true (pl00 fresh start) counts below the tip.
                "training_start": (
                    idx >= TRAINING_START_INDEX
                    or meta.get("training_start") is True
                ),
                "chunk_id": meta.get("chunk_id"),
                "planner_step_index": meta.get("planner_step_index"),
                "runtime": meta.get("runtime"),
                "state_path": state_rel,
                "sidecar_path": side_rel,
                "state_sha256": meta.get("state_sha256"),
                "sidecar_sha256": meta.get("sidecar_sha256"),
                "bytes": meta.get("bytes") or state_p.stat().st_size,
            }
        )
    rows.sort(key=lambda r: int(r["checkpoint_index"]))
    return rows


def rewrite_manifest(project_root: Path | str | None = None) -> dict[str, Any]:
    dest_root = planner_loyal_root(project_root)
    manifest = {
        "schema_version": 1,
        "route_id": ROUTE_ID,
        "training_start_index": TRAINING_START_INDEX,
        "seed_source": str(CRYSTALS_REL),
        "cells": _scan_cells(dest_root),
    }
    dest_root.mkdir(parents=True, exist_ok=True)
    (dest_root / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _pin_file_path(project_root: Path | str | None = None) -> Path | None:
    """Planner-loyal pin file, re-read every reset. Missing file = unpinned."""
    raw = os.environ.get(_PIN_FILE_ENV, _DEFAULT_PIN_FILE).strip() or _DEFAULT_PIN_FILE
    path = Path(raw)
    if not path.is_absolute():
        base = Path(project_root) if project_root is not None else Path.cwd()
        path = base / path
    return path if path.is_file() else None


def _pin_raw(key: str, project_root: Path | str | None = None) -> str | None:
    """File value wins when the key is present (blank clears launcher env)."""
    pin_file = _pin_file_path(project_root)
    if pin_file is not None:
        from re1_rl.yawn_rails import _parse_pin_file

        overrides = _parse_pin_file(pin_file)
        if key in overrides:
            raw = overrides[key]
            return raw if raw else None
    raw = os.environ.get(key, "").strip()
    return raw if raw else None


def _parse_pin_index(raw: str) -> int | None:
    try:
        idx = int(raw, 10)
    except ValueError:
        return None
    return idx if idx >= 0 else None


def _parse_pin_range(raw: str) -> tuple[int, int] | None:
    for sep in ("-", ":", ",", ".."):
        if sep in raw:
            left, right = raw.split(sep, 1)
            break
    else:
        return None
    try:
        lo = int(left.strip(), 10)
        hi = int(right.strip(), 10)
    except ValueError:
        return None
    if lo < 0 or hi < 0:
        return None
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _parse_pin_set(raw: str) -> frozenset[int] | None:
    indices: set[int] = set()
    for part in raw.replace(":", ",").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            idx = int(token, 10)
        except ValueError:
            continue
        if idx >= 0:
            indices.add(idx)
    return frozenset(indices) if indices else None


def _parse_pin_weight_value(raw: str) -> float | None:
    token = raw.strip()
    if not token:
        return None
    pct = token.endswith("%")
    try:
        value = float(token.rstrip("%"))
    except ValueError:
        return None
    if value <= 0.0:
        return None
    if pct or value > 1.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def _pin_set_weight(project_root: Path | str | None = None) -> float | None:
    """Blend weight for ``SET``. Blank = exclusive set (weight 1)."""
    raw = _pin_raw(_PIN_SET_WEIGHT_ENV, project_root)
    if not raw:
        return None
    return _parse_pin_weight_value(raw)


def _parse_pin_weights(raw: str) -> dict[int | str, float] | None:
    """``54:50,rest:50`` or ``5:0.25,11:0.25``. Values normalize to 1."""
    weights: dict[int | str, float] = {}
    for part in raw.replace(";", ",").split(","):
        token = part.strip()
        if not token:
            continue
        for sep in (":", "=", "/"):
            if sep in token:
                left, right = token.split(sep, 1)
                break
        else:
            continue
        try:
            value = float(right.strip().rstrip("%"))
        except ValueError:
            continue
        if value <= 0.0:
            continue
        left_token = left.strip().lower()
        key: int | str
        if left_token in _PIN_REST_KEYS:
            key = "rest"
        else:
            try:
                idx = int(left_token, 10)
            except ValueError:
                continue
            if idx < 0:
                continue
            key = idx
        weights[key] = weights.get(key, 0.0) + value
    if not weights:
        return None
    total = sum(weights.values())
    if total <= 0.0:
        return None
    return {key: weight / total for key, weight in weights.items()}


def reset_pin_allowed_indices(
    project_root: Path | str | None = None,
) -> set[int] | None:
    """Exclusive start indices from the live pin, or ``None`` if mixed/unpinned."""
    index_raw = _pin_raw(_PIN_INDEX_ENV, project_root)
    if index_raw:
        idx = _parse_pin_index(index_raw)
        return {idx} if idx is not None else None
    range_raw = _pin_raw(_PIN_RANGE_ENV, project_root)
    if range_raw:
        span = _parse_pin_range(range_raw)
        if span is None:
            return None
        lo, hi = span
        return set(range(lo, hi + 1))
    if _pin_raw(_PIN_WEIGHTS_ENV, project_root):
        return None
    set_raw = _pin_raw(_PIN_SET_ENV, project_root)
    if set_raw:
        if _pin_set_weight(project_root) is not None:
            return None
        return set(_parse_pin_set(set_raw) or ()) or None
    return None


def _apply_reset_pin(
    cells: list[dict[str, Any]],
    project_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    allowed = reset_pin_allowed_indices(project_root)
    if allowed is None:
        return cells
    pinned = [row for row in cells if int(row["checkpoint_index"]) in allowed]
    if pinned:
        return pinned
    print(
        f"[planner_loyal] reset_pin miss allowed={sorted(allowed)}; "
        f"using unpinned {cell_dir_name(TRAINING_START_INDEX)}+ pool",
        flush=True,
    )
    return cells


def _sample_from_pin_weights(
    cells: list[dict[str, Any]],
    weights: dict[int | str, float],
    rng: random.Random,
) -> dict[str, Any]:
    by_index = {int(row["checkpoint_index"]): row for row in cells}
    named = {
        idx: float(weight)
        for idx, weight in weights.items()
        if isinstance(idx, int) and idx in by_index
    }
    rest_w = float(weights.get("rest") or 0.0)
    others = [row for row in cells if int(row["checkpoint_index"]) not in named]
    if rest_w > 0.0 and others:
        pick_w = [(by_index[idx], named[idx]) for idx in named]
        each_rest = rest_w / len(others)
        pick_w.extend((row, each_rest) for row in others)
    elif named:
        pick_w = [(by_index[idx], named[idx]) for idx in named]
    else:
        return rng.choice(cells)
    total = sum(weight for _row, weight in pick_w)
    if total <= 0.0:
        return rng.choice(cells)
    chosen = rng.choices(
        [row for row, _weight in pick_w],
        weights=[weight / total for _row, weight in pick_w],
        k=1,
    )[0]
    return chosen


def _sample_training_start(
    cells: list[dict[str, Any]],
    project_root: Path | str | None,
    rng: random.Random,
) -> dict[str, Any]:
    weights_raw = _pin_raw(_PIN_WEIGHTS_ENV, project_root)
    if weights_raw and reset_pin_allowed_indices(project_root) is None:
        parsed = _parse_pin_weights(weights_raw)
        if parsed:
            return _sample_from_pin_weights(cells, parsed, rng)
    set_raw = _pin_raw(_PIN_SET_ENV, project_root)
    set_weight = _pin_set_weight(project_root)
    if set_raw and set_weight is not None:
        focused_idx = _parse_pin_set(set_raw) or frozenset()
        focused = [
            row for row in cells if int(row["checkpoint_index"]) in focused_idx
        ]
        others = [
            row for row in cells if int(row["checkpoint_index"]) not in focused_idx
        ]
        if focused and rng.random() < set_weight:
            return rng.choice(focused)
        return rng.choice(others or cells)
    return rng.choice(cells)


def _live_chunk(project_root: Path | str | None = None) -> dict[str, Any] | None:
    try:
        from re1_rl.planner_loyal import load_chunk, resolve_chunk_path

        path = resolve_chunk_path(project_root)
        if not path.is_file():
            return None
        return load_chunk(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _live_chunk_n_steps(project_root: Path | str | None = None) -> int | None:
    chunk = _live_chunk(project_root)
    if chunk is None:
        return None
    return len(chunk.get("steps") or [])


def live_chunk_id(project_root: Path | str | None = None) -> str | None:
    chunk = _live_chunk(project_root)
    if chunk is None:
        return None
    raw = chunk.get("chunk_id")
    return str(raw) if raw else None


def _chunk_mismatch(row: dict[str, Any], live_id: str | None) -> bool:
    """Cell minted by a different chunk than the one loaded now."""
    own = row.get("chunk_id")
    return bool(own) and bool(live_id) and str(own) != str(live_id)


def _row_runtime_matches(row: dict[str, Any]) -> bool:
    """Meta written for the other runtime (pl00 meta describes the C-RE1
    cell.pst; a BizHawk worker sharing the dir must not treat it as a start)."""
    runtime = str(row.get("runtime") or "").strip().lower()
    if not runtime:
        return True
    return (runtime == "recomp") == recomp_cells_enabled()


def seek_index_after_cell(
    row: dict[str, Any],
    live_id: str | None = None,
) -> int:
    """Queue index after reset from this cell (0 = first chunk step).

    ``planner_step_index`` is only meaningful for the chunk that minted the
    cell; under another chunk (``pl06`` lockpick tip minted by the opening
    chunk, then loaded by the live chunk) fall back to the slot rule.
    """
    raw = row.get("planner_step_index")
    if raw is not None and not _chunk_mismatch(row, live_id):
        return int(raw) + 1
    slot = int(row.get("checkpoint_index") or 0)
    if slot <= TRAINING_START_INDEX:
        return 0
    return slot - TRAINING_START_INDEX


def cell_has_remaining_planner_step(
    row: dict[str, Any],
    n_steps: int | None,
    live_id: str | None = None,
) -> bool:
    if not n_steps:
        return True
    return seek_index_after_cell(row, live_id) < int(n_steps)


def iter_training_start_cells(
    project_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Loadable starts: tip (``pl06``) and later cells with a next step,
    plus ``training_start: true`` cells (``pl00``) and pinned slots.

    Cells minted by another chunk only qualify at the tip slot (seek 0);
    mid-chunk step indices do not transfer between chunks.
    """
    root = planner_loyal_root(project_root)
    n_steps = _live_chunk_n_steps(project_root)
    live_id = live_chunk_id(project_root)
    pinned = reset_pin_allowed_indices(project_root) or set()
    out: list[dict[str, Any]] = []
    skipped_foreign = 0
    for row in _scan_cells(root):
        idx = int(row["checkpoint_index"])
        # Only this runtime's payload is loadable (scan lists both runtimes).
        state_p = root / "cells" / cell_dir_name(idx) / cell_state_filename()
        sidecar_p = state_p.with_name(CELL_SIDECAR_NAME)
        if not (state_p.is_file() and sidecar_p.is_file()):
            continue
        if (
            _chunk_mismatch(row, live_id)
            and idx != TRAINING_START_INDEX
            and idx != FRESH_START_INDEX
            and idx not in pinned  # explicit pin wins; seek falls to slot rule
        ):
            skipped_foreign += 1
            continue
        if idx < TRAINING_START_INDEX and not (
            (row.get("training_start") is True or idx in pinned)
            and _row_runtime_matches(row)
        ):
            continue
        if not cell_has_remaining_planner_step(row, n_steps, live_id):
            continue
        out.append(row)
    if skipped_foreign:
        print(
            f"[planner_loyal] skipped {skipped_foreign} cells minted by "
            f"another chunk (live={live_id})",
            flush=True,
        )
    if out:
        return _apply_reset_pin(out, project_root)
    # Fallback: tip even if meta flag drifted.
    tip = training_start_paths(project_root)
    if tip["state"].is_file() and tip["sidecar"].is_file():
        meta: dict[str, Any] = {}
        if tip["meta"].is_file():
            try:
                meta = json.loads(tip["meta"].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        return [
            {
                "checkpoint_index": TRAINING_START_INDEX,
                "checkpoint_id": meta.get("checkpoint_id") or "barry_hall_return_106",
                "room_id": meta.get("room_id") or "106",
                "quality": meta.get("quality") or [],
                "training_start": True,
                "planner_step_index": meta.get("planner_step_index"),
                "state_sha256": meta.get("state_sha256"),
                "sidecar_sha256": meta.get("sidecar_sha256"),
            }
        ]
    return []


def sample_training_start_cell(
    project_root: Path | str | None = None,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    cells = iter_training_start_cells(project_root)
    if not cells:
        return None
    pick = _sample_training_start(cells, project_root, rng or random)
    root = planner_loyal_root(project_root)
    idx = int(pick["checkpoint_index"])
    slot = cell_slot_dir(root, idx)
    return {
        **pick,
        "cell_dir": slot,
        "state": slot / cell_state_filename(),
        "sidecar": slot / CELL_SIDECAR_NAME,
        "meta": slot / CELL_META_NAME,
    }


def _positive_room_counts(raw: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for room, count in raw.items():
        room_id = str(room or "").upper()
        try:
            n = int(count)
        except (TypeError, ValueError):
            continue
        if room_id and n > 0:
            out[room_id] = n
    return out


def _almanac_delta(
    current: dict[str, dict[str, int]],
    predecessor: dict[str, dict[str, int]] | None,
) -> dict[str, dict[str, int]]:
    pred = predecessor or {}
    out: dict[str, dict[str, int]] = {}
    for room, types in current.items():
        if not isinstance(types, dict):
            continue
        bucket: dict[str, int] = {}
        pred_types = pred.get(room) or {}
        for etype, count in types.items():
            try:
                n = int(count) - int(pred_types.get(etype, 0) or 0)
            except (TypeError, ValueError):
                continue
            if n > 0:
                bucket[str(etype)] = n
        if bucket:
            out[str(room)] = bucket
    return out


def _almanac_from_cell_dir(cell_dir: Path) -> dict[str, dict[str, int]]:
    sidecar_p = cell_dir / CELL_SIDECAR_NAME
    if not sidecar_p.is_file():
        return {}
    try:
        data = json.loads(sidecar_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    from re1_rl.pb_sidecar import enemies_killed_from_sidecar

    return enemies_killed_from_sidecar(data)


def planner_loyal_kill_audit(
    progress: Any,
    predecessor_almanac: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """What this stretch thinks it killed. Audit only — not a quality dim."""
    empty = {
        "paid_stretch": 0,
        "paid_stretch_by_room": {},
        "paid_episode": 0,
        "paid_episode_by_room": {},
        "almanac_stretch": 0,
        "almanac_stretch_by_room": {},
        "almanac_total": 0,
        "almanac_total_by_room": {},
    }
    if progress is None:
        return empty
    live = _positive_room_counts(getattr(progress, "leg_kills_by_room", None) or {})
    claimed = _positive_room_counts(
        getattr(progress, "last_claimed_leg_kills", None) or {}
    )
    paid_stretch = live or claimed
    paid_episode = _positive_room_counts(
        getattr(progress, "episode_kills_by_room", None) or {}
    )
    from re1_rl.pb_sidecar import enemies_killed_from_sidecar

    almanac_total = enemies_killed_from_sidecar(
        {"enemies_killed_by_room": getattr(progress, "enemies_killed_by_room", None)}
    )
    almanac_stretch = _almanac_delta(almanac_total, predecessor_almanac)
    return {
        "paid_stretch": sum(paid_stretch.values()),
        "paid_stretch_by_room": paid_stretch,
        "paid_episode": sum(paid_episode.values()),
        "paid_episode_by_room": paid_episode,
        "almanac_stretch": sum(
            sum(types.values()) for types in almanac_stretch.values()
        ),
        "almanac_stretch_by_room": almanac_stretch,
        "almanac_total": sum(sum(types.values()) for types in almanac_total.values()),
        "almanac_total_by_room": almanac_total,
    }


def _fmt_room_kills(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    return ",".join(f"{room}:{n}" for room, n in sorted(counts.items()))


def _fmt_almanac_kills(counts: dict[str, dict[str, int]]) -> str:
    if not counts:
        return "-"
    parts: list[str] = []
    for room, types in sorted(counts.items()):
        for etype, n in sorted(types.items()):
            parts.append(f"{room}:{etype}={n}")
    return ",".join(parts)


def close_planner_loyal_stretch(progress: Any) -> None:
    """Next planner step starts a fresh paid-kill stretch. Almanac stays."""
    if progress is None:
        return
    live = getattr(progress, "leg_kills_by_room", None)
    if isinstance(live, dict):
        live.clear()
    claimed = getattr(progress, "last_claimed_leg_kills", None)
    if isinstance(claimed, dict):
        claimed.clear()


def _drop_kill_insert(vals: list[int]) -> list[int]:
    """Remove the dropped path-kills dim (index 1 or 2 depending on format)."""
    if len(vals) < 3:
        return vals
    # Shipped insert was (hp, kills, ammo, ...). Local remints used
    # (hp, ammo, kills, ...). Kills stay small; ammo/healing do not.
    if vals[1] <= 20 and vals[2] >= 20:
        return [vals[0], *vals[2:]]
    return [vals[0], vals[1], *vals[3:]]


def lift_planner_loyal_quality(
    raw: list[Any] | tuple[Any, ...] | None,
) -> tuple[int, ...]:
    """``(hp, ammo, healing, slots, poison, -ink, -box, -frames)``.

    Path-kills is no longer a quality dim. Strip it from 9-tuples and from
    8-tuples that still have the insert (poison shifted off index 4).
    """
    from re1_rl.go_explore_archive import normalize_quality

    vals = [int(x) for x in list(raw or [])]
    if len(vals) >= 9:
        vals = _drop_kill_insert(vals)
    elif len(vals) == 8 and vals[4] not in (0, 1):
        vals = _drop_kill_insert(vals)
    return tuple(normalize_quality(vals))


def planner_loyal_quality_beats(
    new: list[Any] | tuple[Any, ...] | None,
    old: list[Any] | tuple[Any, ...] | None,
) -> bool:
    """True if *new* should replace *old* (HP, ammo, healing, then the rest)."""
    if old is None:
        return True
    return lift_planner_loyal_quality(new) > lift_planner_loyal_quality(old)


def _preserve_foreign_state(dest: Path, staging: Path) -> None:
    """Carry the other runtime's state file (cell.State vs cell.pst) into
    the replacement so a C-RE1 mint never deletes a BizHawk cell."""
    own = cell_state_filename()
    for name in (CELL_STATE_NAME, _RECOMP_STATE_NAME):
        src = dest / name
        if name != own and src.is_file() and not (staging / name).exists():
            shutil.move(str(src), str(staging / name))


def _nearest_predecessor_slot(root: Path, slot: int) -> int | None:
    """Closest lower slot with a loadable state (skips capture:false holes)."""
    for pred in range(int(slot) - 1, TRAINING_START_INDEX - 1, -1):
        if (cell_slot_dir(root, pred) / cell_state_filename()).is_file():
            return int(pred)
    return None


def capture_planner_loyal_cell(
    env: Any,
    state: dict[str, Any],
    breakdown: dict[str, float],
    *,
    completed_index: int | None = None,
) -> dict[str, Any] | None:
    """Mint a thin ``plNN`` cell for the just-completed planner step.

    ``pl00`` (fresh start) is never minted here; it is installed by hand.
    """
    queue = getattr(env, "_planner_loyal_queue", None)
    if queue is None:
        return None
    if float(breakdown.get("checkpoint_success", 0.0) or 0.0) <= 0.0:
        if float(breakdown.get("planner_step_success", 0.0) or 0.0) <= 0.0:
            return None

    if completed_index is None:
        completed = max(0, int(queue.index) - 1)
    else:
        completed = max(0, int(completed_index))
    n_steps = len(getattr(queue, "_steps", []) or [])
    is_final = n_steps > 0 and completed >= n_steps - 1
    steps = getattr(queue, "_steps", []) or []
    step = dict(steps[completed]) if 0 <= completed < len(steps) else {}
    if step.get("capture") is False:
        print(
            f"[planner_loyal] skip_capture step={completed + 1} "
            f"beat={step.get('beat_id') or step.get('site_id')}",
            flush=True,
        )
        return None
    slot = slot_index_for_completed_step(completed, steps)
    tip = cell_slot_dir(planner_loyal_root(env.project_root), TRAINING_START_INDEX)
    # Opening remint (pl01–pl06) writes the seed slots; does not need the
    # lockpick tip. Live-chunk mints (pl07+) still require pl06 on disk.
    if slot > TRAINING_START_INDEX and not (tip / cell_state_filename()).is_file():
        print(
            f"[planner_loyal] reject missing training tip "
            f"{cell_dir_name(TRAINING_START_INDEX)}",
            flush=True,
        )
        return None
    root = planner_loyal_root(env.project_root)
    pred_slot = _nearest_predecessor_slot(root, slot)
    if slot > TRAINING_START_INDEX and pred_slot is None:
        print(
            f"[planner_loyal] reject missing_predecessor "
            f"{cell_dir_name(slot)} need>={cell_dir_name(TRAINING_START_INDEX)}",
            flush=True,
        )
        return None

    dest = cell_slot_dir(root, slot)
    staging = root / ".staging" / f"{cell_dir_name(slot)}_{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    state_path = staging / cell_state_filename()
    sidecar_path = staging / CELL_SIDECAR_NAME
    pred_almanac: dict[str, dict[str, int]] = {}
    if pred_slot is not None and pred_slot >= TRAINING_START_INDEX:
        pred_almanac = _almanac_from_cell_dir(cell_slot_dir(root, pred_slot))
    kill_audit = planner_loyal_kill_audit(
        getattr(env, "_progress", None), pred_almanac
    )
    try:
        env.bridge.save_savestate(str(state_path))
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError) as exc:
        print(f"[planner_loyal] save_savestate failed: {exc}", flush=True)
        shutil.rmtree(staging, ignore_errors=True)
        return None

    try:
        from re1_rl.pb_sidecar import dump_episode_sidecar, utc_now_iso

        room_hint = str(state.get("room_id") or "")
        sidecar = dump_episode_sidecar(
            env,
            captured_room_id=room_hint or None,
            captured_at_iso=utc_now_iso(),
        )
        sidecar["capture_step"] = int(getattr(env, "_step_count", 0) or 0)
        sidecar["planner_loyal"] = {
            "chunk_id": queue.chunk_id,
            "completed_step_index": completed,
            "slot_index": slot,
            "chunk_final": bool(is_final),
            "kills": kill_audit,
        }
        sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — capture must not kill the env
        print(f"[planner_loyal] sidecar dump failed: {exc}", flush=True)
        shutil.rmtree(staging, ignore_errors=True)
        return None

    from re1_rl.go_explore_archive import attach_leg_frames
    from re1_rl.go_explore_capture import compute_quality

    quality = compute_quality(
        state,
        ever_held=getattr(getattr(env, "_items", None), "ever_held", None),
        env=env,
    )
    try:
        leg_frames = int(getattr(env, "_step_count", 0) or 0)
    except (TypeError, ValueError):
        leg_frames = 0
    quality = list(attach_leg_frames(quality, leg_frames))

    # Quality-beats-old only against a cell of the same runtime; a legacy
    # BizHawk cell.State sharing the slot dir is a different numbering.
    if dest.exists() and (dest / cell_state_filename()).is_file():
        old_meta_p = dest / CELL_META_NAME
        old_q: list[Any] = []
        if old_meta_p.is_file():
            try:
                old_q = list(
                    json.loads(old_meta_p.read_text(encoding="utf-8")).get("quality")
                    or []
                )
            except (OSError, json.JSONDecodeError, TypeError):
                old_q = []
        if old_q and not planner_loyal_quality_beats(quality, old_q):
            print(
                f"[planner_loyal] reject quality {cell_dir_name(slot)} "
                f"new={quality} old={old_q}",
                flush=True,
            )
            shutil.rmtree(staging, ignore_errors=True)
            return None

    last = getattr(env, "_planner_loyal_last_success", None) or {}
    step = None
    steps = getattr(queue, "_steps", [])
    if 0 <= completed < len(steps):
        step = dict(steps[completed])

    room_id = str(state.get("room_id") or last.get("room_id") or "")
    checkpoint_id = (
        f"{queue.chunk_id}_step{completed + 1:02d}"
        if queue.chunk_id
        else f"planner_step_{completed + 1:02d}"
    )
    meta = {
        "route_id": ROUTE_ID,
        "checkpoint_index": slot,
        "checkpoint_id": checkpoint_id,
        "room_id": room_id,
        "chunk_id": queue.chunk_id,
        "planner_step_index": completed,
        "planner_step": step,
        "quality": quality,
        "training_start": True,
        "chunk_final": bool(is_final),
        "kills": kill_audit,
        "state_sha256": _sha256_file(state_path),
        "sidecar_sha256": _sha256_file(sidecar_path),
        "bytes": state_path.stat().st_size,
    }
    (staging / CELL_META_NAME).write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )

    if dest.exists():
        _preserve_foreign_state(dest, staging)
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(dest))
    _strip_fat_artifacts(dest)
    rewrite_manifest(env.project_root)

    proposal: dict[str, Any] = {
        "source": "planner_loyal",
        "route_id": ROUTE_ID,
        "checkpoint_index": slot,
        "checkpoint_id": checkpoint_id,
        "room_id": room_id,
        "chunk_id": queue.chunk_id,
        "planner_step_index": completed,
        "chunk_final": bool(is_final),
        "training_start": True,
        "quality": quality,
        "kills": kill_audit,
        "state_path": str(dest / cell_state_filename()),
        "sidecar_path": str(dest / CELL_SIDECAR_NAME),
        "meta_path": str(dest / CELL_META_NAME),
    }
    from re1_rl.yawn_rails_sync import build_capture_proposal, yawn_rails_sync_enabled

    if yawn_rails_sync_enabled():
        try:
            bundled = build_capture_proposal(
                route_id=ROUTE_ID,
                checkpoint_index=slot,
                checkpoint_id=checkpoint_id,
                room_id=room_id,
                quality=quality,
                state_path=dest / cell_state_filename(),
                sidecar_path=dest / CELL_SIDECAR_NAME,
                worker_id=os.environ.get("MACHINE_NAME"),
            )
            proposal.update(bundled)
            # Keep planner-specific fields after bundle merge.
            proposal["source"] = "planner_loyal"
            proposal["chunk_id"] = queue.chunk_id
            proposal["planner_step_index"] = completed
            proposal["chunk_final"] = bool(is_final)
            proposal["training_start"] = True
            proposal["planner_step"] = step
            proposal["kills"] = kill_audit
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f"[planner_loyal] bundle pack failed: {exc}", flush=True)

    close_planner_loyal_stretch(getattr(env, "_progress", None))
    print(
        f"[planner_loyal] minted {cell_dir_name(slot)} "
        f"chunk={queue.chunk_id} step={completed} room={room_id} "
        f"final={int(is_final)} start=1 q={quality} "
        f"kills_paid={kill_audit['paid_stretch']} "
        f"{_fmt_room_kills(kill_audit['paid_stretch_by_room'])} "
        f"almanac_stretch={kill_audit['almanac_stretch']} "
        f"{_fmt_almanac_kills(kill_audit['almanac_stretch_by_room'])} "
        f"almanac_total={kill_audit['almanac_total']}",
        flush=True,
    )
    return proposal


def training_start_paths(project_root: Path | str | None = None) -> dict[str, Path]:
    root = planner_loyal_root(project_root)
    slot = cell_slot_dir(root, TRAINING_START_INDEX)
    return {
        "root": root,
        "cell_dir": slot,
        "state": slot / cell_state_filename(),
        "sidecar": slot / CELL_SIDECAR_NAME,
        "meta": slot / CELL_META_NAME,
    }

