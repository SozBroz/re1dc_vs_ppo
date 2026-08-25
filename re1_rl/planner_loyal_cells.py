"""Planner-loyal cell archive under ``states/planner_loyal``.

Seed cells ``pl00``..``pl05`` are copied from ``backups/Crystals_in_time``
(cp00..cp05) through Main Hall + Barry lockpick (``barry_hall_return_106``).

Training starts: seed tip ``pl05`` plus every minted non-final step cell
(``pl06+``). The shield-key (final chunk step) cell is never a start —
completing it ends the episode.

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

# Crystals cp00..cp05 → planner-loyal pl00..pl05
SEED_SOURCE_MAX = 5  # inclusive; barry_hall_return_106
TRAINING_START_INDEX = 5  # earliest training start (lockpick tip)

MANIFEST_FILENAME = "manifest.json"
ROUTE_ID = "planner_loyal_v1"
_THIN_ONLY_NAMES = (CELL_STATE_NAME, CELL_SIDECAR_NAME, CELL_META_NAME)


def planner_loyal_root(project_root: Path | str | None = None) -> Path:
    raw = (os.environ.get(_ROOT_ENV) or "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else (Path(project_root or ROOT) / path)
    return Path(project_root or ROOT) / DEFAULT_PLANNER_LOYAL_REL


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


def slot_index_for_completed_step(completed_step_index: int) -> int:
    """Map a completed planner-queue step to the next plNN slot."""
    return int(TRAINING_START_INDEX) + 1 + int(completed_step_index)


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
    """Copy Crystals ``cp00``..``cp{through}`` into thin ``states/planner_loyal`` cells."""
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
        dst = cell_slot_dir(dest_root, src_i)
        if dst.exists() and not force:
            meta_path = dst / CELL_META_NAME
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            else:
                meta = {}
        else:
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
        meta["checkpoint_index"] = src_i
        meta["source"] = {
            "archive": "Crystals_in_time",
            "checkpoint": f"cp{src_i:02d}",
        }
        # Tip pl05 is the earliest start; earlier seeds stay status/archive only.
        meta["training_start"] = src_i == TRAINING_START_INDEX
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
                "checkpoint_index": src_i,
                "checkpoint_id": meta.get("checkpoint_id"),
                "room_id": meta.get("room_id"),
                "training_start": bool(meta.get("training_start")),
                "state_path": f"{DEFAULT_PLANNER_LOYAL_REL}/cells/{cell_dir_name(src_i)}/{CELL_STATE_NAME}",
                "sidecar_path": f"{DEFAULT_PLANNER_LOYAL_REL}/cells/{cell_dir_name(src_i)}/{CELL_SIDECAR_NAME}",
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
        state_p = path / CELL_STATE_NAME
        sidecar_p = path / CELL_SIDECAR_NAME
        if not state_p.is_file() or not sidecar_p.is_file():
            continue
        meta: dict[str, Any] = {}
        if meta_p.is_file():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        rows.append(
            {
                "checkpoint_index": idx,
                "checkpoint_id": meta.get("checkpoint_id") or path.name,
                "room_id": meta.get("room_id"),
                "quality": meta.get("quality") or [],
                "training_start": bool(meta.get("training_start")),
                "chunk_id": meta.get("chunk_id"),
                "planner_step_index": meta.get("planner_step_index"),
                "state_path": f"{DEFAULT_PLANNER_LOYAL_REL}/cells/{path.name}/{CELL_STATE_NAME}",
                "sidecar_path": f"{DEFAULT_PLANNER_LOYAL_REL}/cells/{path.name}/{CELL_SIDECAR_NAME}",
                "state_sha256": meta.get("state_sha256"),
                "sidecar_sha256": meta.get("sidecar_sha256"),
                "bytes": meta.get("bytes"),
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


def iter_training_start_cells(
    project_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Loadable cells marked ``training_start`` (tip + opened non-final CPs)."""
    root = planner_loyal_root(project_root)
    out: list[dict[str, Any]] = []
    for row in _scan_cells(root):
        if not row.get("training_start"):
            continue
        state_p = root / "cells" / cell_dir_name(int(row["checkpoint_index"])) / CELL_STATE_NAME
        sidecar_p = state_p.with_name(CELL_SIDECAR_NAME)
        if state_p.is_file() and sidecar_p.is_file():
            out.append(row)
    if out:
        return out
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
    pick = (rng or random).choice(cells)
    root = planner_loyal_root(project_root)
    idx = int(pick["checkpoint_index"])
    slot = cell_slot_dir(root, idx)
    return {
        **pick,
        "cell_dir": slot,
        "state": slot / CELL_STATE_NAME,
        "sidecar": slot / CELL_SIDECAR_NAME,
        "meta": slot / CELL_META_NAME,
    }


def capture_planner_loyal_cell(
    env: Any,
    state: dict[str, Any],
    breakdown: dict[str, float],
) -> dict[str, Any] | None:
    """Mint a thin ``plNN`` cell for the just-completed planner step."""
    queue = getattr(env, "_planner_loyal_queue", None)
    if queue is None:
        return None
    if float(breakdown.get("checkpoint_success", 0.0) or 0.0) <= 0.0:
        if float(breakdown.get("planner_step_success", 0.0) or 0.0) <= 0.0:
            return None

    completed = max(0, int(queue.index) - 1)
    n_steps = len(getattr(queue, "_steps", []) or [])
    is_final = n_steps > 0 and completed >= n_steps - 1
    slot = slot_index_for_completed_step(completed)
    tip = cell_slot_dir(planner_loyal_root(env.project_root), TRAINING_START_INDEX)
    if not (tip / CELL_STATE_NAME).is_file():
        print(
            f"[planner_loyal] reject missing training tip "
            f"{cell_dir_name(TRAINING_START_INDEX)}",
            flush=True,
        )
        return None
    if slot > TRAINING_START_INDEX + 1:
        pred = cell_slot_dir(planner_loyal_root(env.project_root), slot - 1)
        if not (pred / CELL_STATE_NAME).is_file():
            print(
                f"[planner_loyal] reject missing_predecessor "
                f"{cell_dir_name(slot)} need={cell_dir_name(slot - 1)}",
                flush=True,
            )
            return None

    root = planner_loyal_root(env.project_root)
    dest = cell_slot_dir(root, slot)
    staging = root / ".staging" / f"{cell_dir_name(slot)}_{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    state_path = staging / CELL_STATE_NAME
    sidecar_path = staging / CELL_SIDECAR_NAME
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
        }
        sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — capture must not kill the env
        print(f"[planner_loyal] sidecar dump failed: {exc}", flush=True)
        shutil.rmtree(staging, ignore_errors=True)
        return None

    from re1_rl.go_explore_archive import attach_leg_frames, quality_beats
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

    if dest.exists():
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
        if old_q and not quality_beats(tuple(quality), tuple(old_q)):
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
        # Final shield-key cell is status-only — never a training start.
        "training_start": not bool(is_final),
        "chunk_final": bool(is_final),
        "state_sha256": _sha256_file(state_path),
        "sidecar_sha256": _sha256_file(sidecar_path),
        "bytes": state_path.stat().st_size,
    }
    (staging / CELL_META_NAME).write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(dest))
    _strip_fat_artifacts(dest)
    rewrite_manifest(env.project_root)

    proposal = {
        "source": "planner_loyal",
        "checkpoint_index": slot,
        "checkpoint_id": checkpoint_id,
        "room_id": room_id,
        "chunk_id": queue.chunk_id,
        "planner_step_index": completed,
        "chunk_final": bool(is_final),
        "training_start": not bool(is_final),
        "quality": quality,
        "state_path": str(dest / CELL_STATE_NAME),
        "sidecar_path": str(dest / CELL_SIDECAR_NAME),
        "meta_path": str(dest / CELL_META_NAME),
    }
    print(
        f"[planner_loyal] minted {cell_dir_name(slot)} "
        f"chunk={queue.chunk_id} step={completed} room={room_id} "
        f"final={int(is_final)} start={int(not is_final)} q={quality}",
        flush=True,
    )
    return proposal


def training_start_paths(project_root: Path | str | None = None) -> dict[str, Path]:
    root = planner_loyal_root(project_root)
    slot = cell_slot_dir(root, TRAINING_START_INDEX)
    return {
        "root": root,
        "cell_dir": slot,
        "state": slot / CELL_STATE_NAME,
        "sidecar": slot / CELL_SIDECAR_NAME,
        "meta": slot / CELL_META_NAME,
    }
