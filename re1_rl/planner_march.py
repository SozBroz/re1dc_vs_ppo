"""Planner-loyal tape march: auto roll-forward on HP/ammo/time.

When ``RE1_PLANNER_MARCH=1`` (pin-file key or launcher env), each reset
evaluates one march tick (throttled per process): if the exclusive INDEX pin
is N, the learner mirror lists rows for N and N+1, this box actually holds
the N+1 bytes, and N+1 is better-or-equal on HP[0], ammo[2], and -frames[8],
crystalize N+1 and rewrite the pin INDEX to N+1. One step per tick; chains
cascade across ticks.

Fail-closed everywhere: flag unset, non-exclusive pin (range/set/weights),
env-sourced INDEX (nothing to rewrite), missing rows/live dirs, short
quality, stop bound, lock contention, crystal failure -> no-op.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

# HP / ammo / -frames dims of the lifted 11-dim planner-loyal quality.
_MARCH_DIMS = (0, 2, 8)
_MARCH_ENV = "RE1_PLANNER_MARCH"
_MARCH_STOP_ENV = "RE1_PLANNER_MARCH_STOP"
_MARCH_MIN_S_ENV = "RE1_PLANNER_MARCH_MIN_S"
_PIN_INDEX_KEY = "RE1_PLANNER_RESET_PIN_INDEX"
_DEFAULT_MIN_S = 60.0
_CRYSTAL_REL = Path("backups/Crystals_in_time/planner_rooms")

_LAST_TICK: dict[str, float] = {}


def _march_flag(project_root: Path | str | None) -> bool:
    from re1_rl.planner_loyal_cells import _pin_raw

    raw = _pin_raw(_MARCH_ENV, project_root)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _march_stop(project_root: Path | str | None) -> int | None:
    from re1_rl.planner_loyal_cells import _pin_raw

    raw = _pin_raw(_MARCH_STOP_ENV, project_root)
    if raw is None:
        return None
    try:
        stop = int(raw.strip(), 10)
    except ValueError:
        return None
    return stop if stop >= 0 else None


def _march_min_s(project_root: Path | str | None) -> float:
    from re1_rl.planner_loyal_cells import _pin_raw

    raw = _pin_raw(_MARCH_MIN_S_ENV, project_root)
    if raw is None:
        return _DEFAULT_MIN_S
    try:
        return max(5.0, float(raw.strip()))
    except ValueError:
        return _DEFAULT_MIN_S


def _file_pin_index(pin_file: Path) -> int | None:
    """Exclusive INDEX from the pin *file* (env-sourced pins are not rewritable)."""
    from re1_rl.planner_loyal_cells import _parse_pin_index
    from re1_rl.yawn_rails import _parse_pin_file

    try:
        overrides = _parse_pin_file(pin_file)
    except OSError:
        return None
    raw = overrides.get(_PIN_INDEX_KEY)
    if not raw:
        return None
    return _parse_pin_index(raw.strip())


def _mirror_rows(project_root: Path | str) -> dict[int, dict[str, Any]]:
    from re1_rl.yawn_rails_worker_cache import load_local_yawn_manifest

    try:
        manifest = load_local_yawn_manifest(project_root)
    except (OSError, ValueError, TypeError):
        return {}
    rows: dict[int, dict[str, Any]] = {}
    for row in manifest.get("cells") or []:
        if not isinstance(row, dict):
            continue
        try:
            rows[int(row["checkpoint_index"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _march_qualifies(new_q: Any, old_q: Any) -> bool:
    """Better-or-equal on HP, ammo, -frames. Short quality never qualifies."""
    from re1_rl.planner_loyal_cells import lift_planner_loyal_quality

    try:
        new = lift_planner_loyal_quality(new_q)
        old = lift_planner_loyal_quality(old_q)
    except (TypeError, ValueError):
        return False
    if len(new_q or []) < 11 or len(old_q or []) < 11:
        return False
    return all(new[d] >= old[d] for d in _MARCH_DIMS)


def _crystalize(project_root: Path, idx: int) -> Path:
    """Copy live ``plNN`` into Crystals_in_time (same shape as hand crystals)."""
    from re1_rl.planner_loyal_cells import cell_dir_name, planner_loyal_root
    from re1_rl.yawn_rails_sync import slot_state_path

    root = planner_loyal_root(project_root)
    src = root / "cells" / cell_dir_name(idx)
    state_p = slot_state_path(src)
    meta_p = src / "meta.json"
    if state_p is None or not meta_p.is_file():
        raise FileNotFoundError(f"crystal source incomplete: {src}")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    prev_meta_p = root / "cells" / cell_dir_name(idx - 1) / "meta.json"
    if not prev_meta_p.is_file():
        raise FileNotFoundError(f"crystal chain base missing: {prev_meta_p}")
    prev_meta = json.loads(prev_meta_p.read_text(encoding="utf-8"))
    dst = Path(project_root) / _CRYSTAL_REL / f"{cell_dir_name(idx)}_solo"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    (dst / "crystal_meta.json").write_text(
        json.dumps(
            {
                "unit": f"{cell_dir_name(idx)}_solo",
                "to_pl_slot": int(idx),
                "from_pl_slot": int(idx) - 1,
                "chain_ok": True,
                "forced": False,
                "to_state_sha256": str(meta.get("state_sha256") or ""),
                "from_state_sha256": str(prev_meta.get("state_sha256") or ""),
                "tape_to_state_sha256": str(meta.get("state_sha256") or ""),
                "room_segment_id": None,
                "has_policy": (dst / "leg_policy.npz").is_file(),
                "installed_unix": int(time.time()),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return dst


def _rewrite_pin_index(pin_file: Path, new_idx: int) -> None:
    lines = pin_file.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if (
            not replaced
            and stripped.startswith(_PIN_INDEX_KEY)
            and "=" in stripped
            and not stripped.startswith("#")
        ):
            out.append(f"{_PIN_INDEX_KEY}={int(new_idx)}\n")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        raise ValueError(f"{_PIN_INDEX_KEY} not found in {pin_file}")
    tmp = pin_file.with_name(f".{pin_file.name}.{os.getpid()}.tmp")
    tmp.write_text("".join(out), encoding="utf-8")
    os.replace(tmp, pin_file)
    _refresh_march_comment(pin_file, new_idx)


def _refresh_march_comment(pin_file: Path, new_idx: int) -> None:
    try:
        lines = pin_file.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return
    out: list[str] = []
    done = False
    for line in lines:
        if not done and line.strip().startswith("# March mode:"):
            out.append(f"# March mode (auto): 100% starts on pl{int(new_idx):02d}.\n")
            done = True
        else:
            out.append(line)
    if not done:
        return
    try:
        tmp = pin_file.with_name(f".{pin_file.name}.{os.getpid()}.tmp")
        tmp.write_text("".join(out), encoding="utf-8")
        os.replace(tmp, pin_file)
    except OSError:
        pass


def maybe_advance_planner_march(project_root: Path | str | None) -> dict[str, Any] | None:
    """One march tick. Returns a record on advance, ``None`` otherwise."""
    if project_root is None:
        return None
    root_key = str(project_root)
    now = time.monotonic()
    if now - _LAST_TICK.get(root_key, 0.0) < _march_min_s(project_root):
        return None
    _LAST_TICK[root_key] = now
    if not _march_flag(project_root):
        return None
    from re1_rl.planner_loyal_cells import (
        _pin_file_path,
        cell_dir_name,
        planner_loyal_root,
    )
    from re1_rl.yawn_rails_sync import slot_matches_content, yawn_cells_locked

    pin_file = _pin_file_path(project_root)
    if pin_file is None:
        return None
    pin = _file_pin_index(pin_file)
    if pin is None:
        return None
    stop = _march_stop(project_root)
    if stop is not None and pin >= stop:
        return None
    rows = _mirror_rows(project_root)
    tip_row = rows.get(pin)
    next_row = rows.get(pin + 1)
    if tip_row is None or next_row is None:
        return None
    if not _march_qualifies(next_row.get("quality"), tip_row.get("quality")):
        return None
    # The box must actually hold the hunted bytes (stale rows carry no bundle).
    slot = planner_loyal_root(project_root) / "cells" / cell_dir_name(pin + 1)
    try:
        if not slot_matches_content(
            slot,
            state_sha256=str(next_row.get("state_sha256") or ""),
            sidecar_sha256=str(next_row.get("sidecar_sha256") or ""),
        ):
            return None
    except (OSError, TypeError, ValueError):
        return None
    # Serialize march decisions across envs on this box; recheck under lock.
    try:
        with yawn_cells_locked(
            planner_loyal_root(project_root),
            holder=f"planner_march:{os.getpid()}",
            timeout_s=5.0,
        ):
            pin_now = _file_pin_index(pin_file)
            if pin_now != pin:
                return None
            rows_now = _mirror_rows(project_root)
            tip_now = rows_now.get(pin)
            next_now = rows_now.get(pin + 1)
            if tip_now is None or next_now is None:
                return None
            if not _march_qualifies(next_now.get("quality"), tip_now.get("quality")):
                return None
            try:
                crystal_dir = _crystalize(Path(project_root), pin + 1)
            except (OSError, ValueError, KeyError) as exc:
                print(f"[planner_march] crystal failed, holding pin {pin}: {exc}", flush=True)
                return None
            _rewrite_pin_index(pin_file, pin + 1)
    except TimeoutError:
        return None
    except (OSError, ValueError) as exc:
        print(f"[planner_march] advance failed, holding pin {pin}: {exc}", flush=True)
        return None
    print(
        f"[planner_march] rolled forward pin {pin} -> {pin + 1} "
        f"({cell_dir_name(pin + 1)} crystal: {crystal_dir.name})",
        flush=True,
    )
    return {"advanced": True, "from_pin": pin, "to_pin": pin + 1}
