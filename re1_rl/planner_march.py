"""Planner-loyal tape march: hard-grind 100% on one PL until cell qualifies.

When ``RE1_PLANNER_MARCH=1`` (pin-file key or launcher env), each reset
evaluates one march tick (throttled per process). The exclusive INDEX pin is
N; every box grinds 100% starts on ``plN`` hunting ``pl(N+1)``. Advance needs
ALL of:

* the pin has sat on N for ``RE1_PLANNER_MARCH_MIN_S`` (default 600s: minimum
  10 minutes of grind per PL, even if the target falls early),
* the learner mirror admits N+1 AND this box holds those exact bytes,
* live ``pl(N+1)`` meta ``quality`` meets the kit bar: HP[0]/kills[1]
  equal-or-greater than the pre-march champion row. **Combat-outcome** hops
  (kills up or HP down) also need ammo_dmg_weighted[2] within
  ``MARCH_AMMO_SLACK`` (default **3**) of champion and hop frames[8] within
  ``RE1_PLANNER_MARCH_FRAMES_FACTOR`` of the resources.md Frames column.
  **Nav / ammo-only** hops skip ammo *and* the frames bar so gallery,
  crest-exit, and one-bullet drips can auto-roll until the next real fight
  (e.g. dogs after ``place_star_crest``). Short quality never qualifies.
  Combat-outcome hops with missing resources Frames still fail closed.

On advance **or any INDEX pin move** (manual edit included): crystalize only
on advance; always request an NN weight reset on the learner (POST
``/march/reset_weights``). When ``data/planner_mint_policy_map.json`` (or a
still-live cell meta) names a ``policy_version`` for the hunt target
``pl(N+1)`` *and* ``data/mint_policies/weights/v<V>.pt`` exists, the reset
loads that mint archive; otherwise it falls back to the march base zip.
Idempotent per advance id; retried until the learner reports applied.

Fail-closed everywhere: flag unset, non-exclusive pin, env-sourced INDEX
(nothing to rewrite), short grind time, missing champion/resources row,
missing rows/live dirs, stale bytes, stop bound, lock contention, crystal
failure, unreachable learner -> no-op (pin holds, grind continues).
"""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any

# HP / kills / ammo_dmg_weighted dims vs champion; frames vs resources.md.
# quality[2] may trail resources.md pistol counts by a couple rounds (RNG /
# pickup timing); accept that slack instead of blocking the march.
MARCH_AMMO_SLACK = 3
# Optional raise on the ammo floor (pin ``RE1_PLANNER_MARCH_AMMO_EXTRA``).
# EXTRA=10 means "end with ~10 more rounds / use ~10 fewer bullets" vs the
# champion bar after slack: live[2] >= champ[2] - SLACK + EXTRA.
_MARCH_AMMO_EXTRA_ENV = "RE1_PLANNER_MARCH_AMMO_EXTRA"
# quality[2] is the mint-time damage-weighted ammo scalar, so pistol-vs-shotgun
# weighting is already settled at capture; the gate just compares scalars.
_MARCH_KIT_DIMS = (0, 1, 2)
_MARCH_FRAMES_DIM = 8
_MARCH_ENV = "RE1_PLANNER_MARCH"
_MARCH_STOP_ENV = "RE1_PLANNER_MARCH_STOP"
_MARCH_MIN_S_ENV = "RE1_PLANNER_MARCH_MIN_S"
_MARCH_FRAMES_FACTOR_ENV = "RE1_PLANNER_MARCH_FRAMES_FACTOR"
_MARCH_KEYS = (
    _MARCH_ENV,
    _MARCH_STOP_ENV,
    _MARCH_MIN_S_ENV,
    _MARCH_FRAMES_FACTOR_ENV,
    _MARCH_AMMO_EXTRA_ENV,
)
_PIN_INDEX_KEY = "RE1_PLANNER_RESET_PIN_INDEX"
_DEFAULT_MIN_S = 600.0
_DEFAULT_FRAMES_FACTOR = 1.1
_CHAMPIONS_REL = Path("data/planner_march_champions.json")
_RESOURCES_REL = Path("docs/planner_loyal_resources.md")
_STATE_REL = Path("data/planner_march_state.json")
_CRYSTAL_REL = Path("backups/Crystals_in_time/planner_rooms")
_RESET_TIMEOUT_S = 10.0
_RESET_RETRY_S = 30.0

_LAST_TICK: dict[str, float] = {}
_CHAMPIONS_CACHE: dict[str, Any] = {}
_RESOURCES_CACHE: dict[str, Any] = {}


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


_MARCH_MIN_S_PER_FRAME_ENV = "RE1_PLANNER_MARCH_MIN_S_PER_FRAME"


def _march_min_s_for_pin(pin: int, project_root: Path | str | None) -> float:
    """Dwell gate for pin N: flat MIN_S, or PER_FRAME × target's recorded frames.

    ``RE1_PLANNER_MARCH_MIN_S_PER_FRAME=1.2`` makes the grind proportional to
    hop length (short hops roll fast, combat hops get real grind). Unknown
    target (first mint) falls back to the flat MIN_S. Floor 5s.
    """
    base = _march_min_s(project_root)
    from re1_rl.planner_loyal_cells import _pin_raw

    try:
        per_frame = float(
            (_pin_raw(_MARCH_MIN_S_PER_FRAME_ENV, project_root) or "").strip() or 0.0
        )
    except (TypeError, ValueError, AttributeError):
        return base
    if per_frame <= 0:
        return base
    try:
        from re1_rl.planner_hop_score import recorded_frames_for_pl
        from re1_rl.planner_loyal_cells import planner_loyal_root

        recorded = int(recorded_frames_for_pl(int(pin) + 1, planner_loyal_root(project_root)))
    except (OSError, ValueError, TypeError):
        return base
    if recorded <= 0:
        return base
    return max(5.0, float(per_frame) * float(recorded))


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


def _champions(project_root: Path | str) -> dict[int, list[int]]:
    """{slot: 11-dim champion quality}. Cached per process; reloads on change."""
    path = Path(project_root) / _CHAMPIONS_REL
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _CHAMPIONS_CACHE.get(str(path))
    if isinstance(cached, dict) and cached.get("mtime") == mtime:
        return cached["cells"]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cells: dict[int, list[int]] = {}
    raw_cells = doc.get("cells") if isinstance(doc, dict) else None
    if isinstance(raw_cells, dict):
        for key, val in raw_cells.items():
            try:
                idx = int(key)
                q = [int(x) for x in list(val)]
            except (TypeError, ValueError):
                continue
            if len(q) >= 11:
                cells[idx] = q[:11]
    _CHAMPIONS_CACHE[str(path)] = {"mtime": mtime, "cells": cells}
    return cells


def _march_frames_factor(project_root: Path | str | None) -> float:
    """Max live hop frames as a multiple of resources.md Frames (default 1.1)."""
    from re1_rl.planner_loyal_cells import _pin_raw

    raw = _pin_raw(_MARCH_FRAMES_FACTOR_ENV, project_root)
    if raw is None:
        return float(_DEFAULT_FRAMES_FACTOR)
    try:
        return max(1.0, float(raw.strip()))
    except ValueError:
        return float(_DEFAULT_FRAMES_FACTOR)


def _march_ammo_extra(project_root: Path | str | None = None) -> int:
    """Extra rounds required above (champ − slack). Pin/env; default 0."""
    from re1_rl.planner_loyal_cells import _pin_raw

    raw = _pin_raw(_MARCH_AMMO_EXTRA_ENV, project_root)
    if raw is None:
        raw = (os.environ.get(_MARCH_AMMO_EXTRA_ENV) or "").strip() or None
    if raw is None:
        return 0
    try:
        return max(0, int(float(str(raw).strip())))
    except (TypeError, ValueError):
        return 0


def march_ammo_floor(
    champ_ammo: int,
    *,
    project_root: Path | str | None = None,
    slack: int | None = None,
    extra: int | None = None,
) -> int:
    """Minimum live quality[2] to pass the fighting-hop ammo bar."""
    s = int(MARCH_AMMO_SLACK if slack is None else slack)
    e = int(_march_ammo_extra(project_root) if extra is None else extra)
    return int(champ_ammo) - int(s) + int(e)


def _resources_frames(project_root: Path | str) -> dict[int, int]:
    """``{slot: Frames}`` from ``docs/planner_loyal_resources.md`` Kit-by-PL table."""
    path = Path(project_root) / _RESOURCES_REL
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _RESOURCES_CACHE.get(str(path))
    if isinstance(cached, dict) and cached.get("mtime") == mtime:
        return cached["frames"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    frames: dict[int, int] = {}
    in_kit = False
    for ln in text.splitlines():
        s = ln.strip()
        if s == "## Kit by PL":
            in_kit = True
            continue
        if in_kit and s.startswith("## "):
            break
        if not in_kit or not s.startswith("|") or s.startswith("| :") or s.startswith("| PL"):
            continue
        parts = [p.strip() for p in s.strip("|").split("|")]
        if len(parts) < 8:
            continue
        name = parts[0].strip("`")
        if not name.startswith("pl") or not name[2:].isdigit():
            continue
        raw = parts[7].strip()
        if not raw:
            continue
        try:
            frames[int(name[2:])] = int(raw)
        except ValueError:
            continue
    _RESOURCES_CACHE[str(path)] = {"mtime": mtime, "frames": frames}
    return frames


def _champion_qualifies(
    live_q: Any,
    champ_q: Any,
    *,
    resources_frames: int = 0,
    frames_factor: float = 1.1,
    require_ammo: bool = True,
    project_root: Path | str | None = None,
) -> bool:
    """Live mint meets kit bar: HP/kills/(ammo) vs champion, frames vs resources.

    ``require_ammo`` is True for combat-outcome hops (ammo slack + frames
    budget). Nav / ammo-only hops skip ammo *and* frames so a sticky HG-eq
    shortfall or a slow gallery mint cannot strand the march before the next
    real fight. ``resources_frames`` is the docs Frames column (policy
    decisions, same unit as ``-quality[8]``). Combat-outcome hops with
    missing/non-positive resources Frames never qualify. ``frames_factor``
    defaults to 1.1. ``RE1_PLANNER_MARCH_AMMO_EXTRA`` raises the ammo floor
    (use fewer bullets / keep more rounds than champ−slack).
    """
    import math

    from re1_rl.planner_loyal_cells import lift_planner_loyal_quality

    if len(live_q or []) < 11 or len(champ_q or []) < 11:
        return False
    rec = int(resources_frames)
    if require_ammo and rec <= 0:
        return False
    try:
        live = lift_planner_loyal_quality(live_q)
        champ = lift_planner_loyal_quality(champ_q)
    except (TypeError, ValueError):
        return False
    # HP + kills must meet champion; ammo (with slack/extra) only on fighting hops.
    if int(live[0]) < int(champ[0]) or int(live[1]) < int(champ[1]):
        return False
    if require_ammo and int(live[2]) < march_ammo_floor(
        int(champ[2]), project_root=project_root
    ):
        return False
    live_frames = -int(live[_MARCH_FRAMES_DIM])
    if live_frames <= 0:
        return False
    if not require_ammo:
        return True
    budget = int(math.ceil(float(rec) * float(frames_factor)))
    return int(live_frames) <= int(budget)


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


def _live_quality(project_root: Path, idx: int) -> list[Any] | None:
    """Quality vector from this box's live ``plNN`` meta (None if unreadable)."""
    from re1_rl.planner_loyal_cells import cell_dir_name, planner_loyal_root

    meta_p = planner_loyal_root(project_root) / "cells" / cell_dir_name(idx) / "meta.json"
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    q = meta.get("quality")
    return list(q) if isinstance(q, (list, tuple)) else None


def _read_march_state(project_root: Path | str) -> dict[str, Any]:
    try:
        doc = json.loads((Path(project_root) / _STATE_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_march_state(project_root: Path | str, state: dict[str, Any]) -> None:
    path = Path(project_root) / _STATE_REL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        print(f"[planner_march] state write failed: {exc}", flush=True)


def _learner_addr() -> tuple[str, int] | None:
    host = (
        os.environ.get("RE1_LEARNER_HOST")
        or os.environ.get("FLEET_LEARNER_HOST")
        or ""
    ).strip()
    port_raw = (
        os.environ.get("RE1_LEARNER_PORT")
        or os.environ.get("FLEET_LEARNER_PORT")
        or "8765"
    ).strip()
    if not host:
        return None
    try:
        return host, int(port_raw)
    except ValueError:
        return None


def _request_learner_reset(
    host: str,
    port: int,
    advance_id: str,
    *,
    mint_policy_version: int | None = None,
) -> bool:
    """POST /march/reset_weights. True only on an explicit ok."""
    payload: dict[str, Any] = {"advance_id": advance_id}
    if mint_policy_version is not None and int(mint_policy_version) > 0:
        payload["mint_policy_version"] = int(mint_policy_version)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/march/reset_weights",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_RESET_TIMEOUT_S) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[planner_march] reset request {advance_id} failed: {exc}", flush=True)
        return False
    ok = isinstance(response, dict) and response.get("ok") is True
    if not ok:
        print(
            f"[planner_march] reset request {advance_id} refused: {response!r}",
            flush=True,
        )
    return ok


def _learner_reset_applied(host: str, port: int) -> str | None:
    """Last applied march-reset advance id from learner /status (None if unknown)."""
    try:
        with urllib.request.urlopen(
            f"http://{host}:{port}/status", timeout=_RESET_TIMEOUT_S
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    block = payload.get("march_reset")
    if not isinstance(block, dict):
        return None
    applied = block.get("last_applied_advance_id")
    return str(applied) if applied else None


def _hunt_mint_version(project_root: Path | str, pin_idx: int) -> int | None:
    """Archive version for hunting ``pl(pin+1)``, or None → base ckpt."""
    try:
        from re1_rl.mint_policy_map import resolve_hunt_mint_version

        return resolve_hunt_mint_version(int(pin_idx), project_root=project_root)
    except Exception:  # noqa: BLE001 — never block march on map I/O
        return None


def _queue_pin_reset(
    project_root: Path | str,
    *,
    advance_id: str,
    pin_idx: int,
    wall_now: float,
    extra: dict[str, Any] | None = None,
    mint_policy_version: int | None = None,
) -> None:
    """Persist pending NN reset + pin clock; pump once."""
    state = _read_march_state(project_root)
    if mint_policy_version is None:
        mint_policy_version = _hunt_mint_version(project_root, int(pin_idx))
    pending: dict[str, Any] = {
        "advance_id": str(advance_id),
        "requested_unix": wall_now,
        "last_attempt_unix": 0.0,
    }
    if mint_policy_version is not None and int(mint_policy_version) > 0:
        pending["mint_policy_version"] = int(mint_policy_version)
        print(
            f"[planner_march] NN reset {advance_id} will load mint "
            f"v{int(mint_policy_version)} (hunt pl{int(pin_idx) + 1:02d})",
            flush=True,
        )
    else:
        print(
            f"[planner_march] NN reset {advance_id} will load march base "
            f"(no mint archive for hunt pl{int(pin_idx) + 1:02d})",
            flush=True,
        )
    payload = {
        **state,
        "pin_idx": int(pin_idx),
        "pin_since_unix": wall_now,
        "pending_reset": pending,
    }
    if extra:
        payload.update(extra)
    _write_march_state(project_root, payload)
    _pump_pending_reset(project_root, wall_now)


def _pump_pending_reset(project_root: Path | str, now: float) -> None:
    """Retry/ack the outstanding NN reset request (best effort, never raises)."""
    state = _read_march_state(project_root)
    pending = state.get("pending_reset")
    if not isinstance(pending, dict):
        return
    advance_id = str(pending.get("advance_id") or "")
    if not advance_id:
        state.pop("pending_reset", None)
        _write_march_state(project_root, state)
        return
    addr = _learner_addr()
    if addr is None:
        print(
            "[planner_march] no learner address (RE1_LEARNER_HOST); "
            f"reset {advance_id} stays pending",
            flush=True,
        )
        return
    host, port = addr
    try:
        if _learner_reset_applied(host, port) == advance_id:
            state.pop("pending_reset", None)
            _write_march_state(project_root, state)
            print(f"[planner_march] learner confirmed NN reset {advance_id}", flush=True)
            return
    except (OSError, ValueError) as exc:
        print(f"[planner_march] reset ack check failed: {exc}", flush=True)
    last_attempt = pending.get("last_attempt_unix")
    try:
        due = now - float(last_attempt or 0) >= _RESET_RETRY_S
    except (TypeError, ValueError):
        due = True
    if not due:
        return
    mint_ver = None
    raw_ver = pending.get("mint_policy_version")
    if raw_ver is not None and raw_ver != "":
        try:
            mint_ver = int(raw_ver)
        except (TypeError, ValueError):
            mint_ver = None
        if mint_ver is not None and mint_ver <= 0:
            mint_ver = None
    if _request_learner_reset(
        host, port, advance_id, mint_policy_version=mint_ver
    ):
        print(
            f"[planner_march] reset {advance_id} (re)sent to learner"
            + (f" mint_v={mint_ver}" if mint_ver is not None else ""),
            flush=True,
        )
    pending["last_attempt_unix"] = now
    state["pending_reset"] = pending
    _write_march_state(project_root, state)


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
    # Always pull fleet master pin *before* reading MARCH/INDEX. Hosts stuck on
    # a stale local MARCH=0 previously skipped sync forever and kept grinding a
    # divergent INDEX (fleet desync: pl58/59 while master hunted pl57).
    try:
        from re1_rl.distributed.march_pin import sync_pin_from_learner

        sync_pin_from_learner(project_root)
    except Exception as exc:  # noqa: BLE001 — never block the hunt on pin sync
        print(f"[planner_march] pin sync skipped: {exc}", flush=True)
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
    wall_now = time.time()
    # Keep the outstanding NN reset moving even while grinding.
    _pump_pending_reset(project_root, wall_now)
    # Minimum grind time per PL: first sighting of this pin starts the clock.
    # Any INDEX move (manual or prior advance on another box) also queues an
    # NN base reset so overfit weights do not carry into the new tip.
    state = _read_march_state(project_root)
    prev_pin = state.get("pin_idx")
    pin_since = state.get("pin_idx") == pin and state.get("pin_since_unix") or None
    try:
        pin_since_f = float(pin_since) if pin_since is not None else 0.0
    except (TypeError, ValueError):
        pin_since_f = 0.0
    try:
        prev_pin_i = int(prev_pin) if prev_pin is not None else None
    except (TypeError, ValueError):
        prev_pin_i = None
    if prev_pin_i is not None and prev_pin_i != pin:
        advance_id = f"pin-move-{prev_pin_i:02d}-to-{pin:02d}"
        print(
            f"[planner_march] pin moved {prev_pin_i} -> {pin}; "
            f"requesting NN reset {advance_id}",
            flush=True,
        )
        # Manual / synced pin move: publish local truth so the fleet converges.
        try:
            from re1_rl.distributed.march_pin import publish_local_pin

            publish_local_pin(project_root, force=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[planner_march] pin publish skipped: {exc}", flush=True)
        _queue_pin_reset(
            project_root,
            advance_id=advance_id,
            pin_idx=pin,
            wall_now=wall_now,
        )
        return None
    if state.get("pin_idx") != pin or pin_since_f <= 0:
        _write_march_state(
            project_root,
            {**state, "pin_idx": pin, "pin_since_unix": wall_now},
        )
        return None
    if wall_now - pin_since_f < _march_min_s_for_pin(pin, project_root):
        return None
    nxt = pin + 1
    champ = _champions(project_root).get(nxt)
    if champ is None:
        print(f"[planner_march] no champion row for pl{nxt:02d}; holding pin {pin}", flush=True)
        return None
    rec_frames = int(_resources_frames(project_root).get(nxt) or 0)
    if rec_frames <= 0:
        print(
            f"[planner_march] no resources Frames for pl{nxt:02d}; holding pin {pin}",
            flush=True,
        )
        return None
    frames_factor = _march_frames_factor(project_root)
    try:
        from re1_rl.fight_pl_policy import (
            march_frames_factor_for_target,
            march_requires_kit_bar,
        )

        frames_factor = float(
            march_frames_factor_for_target(
                nxt, project_root, base_factor=float(frames_factor)
            )
        )
        # Ammo-only drips stay nav-qualify; kills/HP changes keep the full bar.
        require_ammo = bool(march_requires_kit_bar(pin, nxt, project_root))
    except Exception:  # noqa: BLE001 — never block march on policy import
        require_ammo = True
    rows = _mirror_rows(project_root)
    next_row = rows.get(nxt)
    if next_row is None:
        return None
    # The box must actually hold the hunted bytes (stale rows carry no bundle).
    slot = planner_loyal_root(project_root) / "cells" / cell_dir_name(nxt)
    try:
        if not slot_matches_content(
            slot,
            state_sha256=str(next_row.get("state_sha256") or ""),
            sidecar_sha256=str(next_row.get("sidecar_sha256") or ""),
        ):
            return None
    except (OSError, TypeError, ValueError):
        return None
    live_q = _live_quality(Path(project_root), nxt)
    if live_q is None or not _champion_qualifies(
        live_q,
        champ,
        resources_frames=rec_frames,
        frames_factor=frames_factor,
        require_ammo=require_ammo,
        project_root=project_root,
    ):
        return None
    advance_id = f"pl{pin:02d}->pl{nxt:02d}@{int(wall_now)}"
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
            live_now = _live_quality(Path(project_root), nxt)
            if live_now is None or not _champion_qualifies(
                live_now,
                champ,
                resources_frames=rec_frames,
                frames_factor=frames_factor,
                require_ammo=require_ammo,
                project_root=project_root,
            ):
                return None
            try:
                crystal_dir = _crystalize(Path(project_root), nxt)
            except (OSError, ValueError, KeyError) as exc:
                print(f"[planner_march] crystal failed, holding pin {pin}: {exc}", flush=True)
                return None
            # Publish INDEX to learner first so the whole fleet sees the move;
            # local rewrite follows from the returned master text (or offline).
            try:
                from re1_rl.distributed.march_pin import (
                    fetch_master_pin,
                    publish_index_advance,
                )

                expected_version = None
                snap = fetch_master_pin()
                if snap and snap.get("version") is not None:
                    try:
                        expected_version = int(snap["version"])
                    except (TypeError, ValueError):
                        expected_version = None
                pub = publish_index_advance(
                    project_root,
                    new_idx=nxt,
                    expected_index=pin,
                    expected_version=expected_version,
                )
                if pub is None or pub.get("ok") is not True:
                    print(
                        f"[planner_march] master pin CAS failed, holding pin {pin}: "
                        f"{pub!r}",
                        flush=True,
                    )
                    return None
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[planner_march] master pin publish failed, holding pin {pin}: {exc}",
                    flush=True,
                )
                return None
            # Ensure local INDEX matches even if publish returned offline/no text.
            if _file_pin_index(pin_file) != nxt:
                _rewrite_pin_index(pin_file, nxt)
            mint_ver = _hunt_mint_version(project_root, int(nxt))
            pending_reset: dict[str, Any] = {
                "advance_id": advance_id,
                "requested_unix": wall_now,
                "last_attempt_unix": 0.0,
            }
            if mint_ver is not None:
                pending_reset["mint_policy_version"] = int(mint_ver)
            _write_march_state(
                project_root,
                {
                    "pin_idx": nxt,
                    "pin_since_unix": wall_now,
                    "last_advance_unix": wall_now,
                    "last_advance_id": advance_id,
                    "pending_reset": pending_reset,
                },
            )
    except TimeoutError:
        return None
    except (OSError, ValueError) as exc:
        print(f"[planner_march] advance failed, holding pin {pin}: {exc}", flush=True)
        return None
    print(
        f"[planner_march] rolled forward pin {pin} -> {nxt} "
        f"({cell_dir_name(nxt)} crystal: {crystal_dir.name}; "
        f"frames<={frames_factor:g}x{rec_frames}); "
        f"requesting NN reset {advance_id}",
        flush=True,
    )
    _pump_pending_reset(project_root, wall_now)
    return {"advanced": True, "from_pin": pin, "to_pin": nxt, "advance_id": advance_id}
