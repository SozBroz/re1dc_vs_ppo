"""Curated one-leg reset cells for the Yawn rails curriculum."""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import uuid
from pathlib import Path
from typing import Any

from re1_rl.inventory_stacking import apply_stack_transfer
from re1_rl.item_box import is_box_room
from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import INVENTORY_SLOTS, ITEM_IDS
from re1_rl.pb_sidecar import dump_episode_sidecar, utc_now_iso

_ITEM_NAME_TO_ID = {canonical_item(name): item_id for item_id, name in ITEM_IDS.items()}

# Minimum on-person empty slots required to admit a curated capture (cp42 needs
# headroom for box withdraw/deposit after the storeroom chemical leg).
CAPTURE_MIN_FREE_SLOTS: dict[str, int] = {
    "east_stairs_101_post_storeroom": 2,
    "west_stairs_return_10B": 2,
}

_CAPTURE_INELIGIBLE_ATTR = "_yawn_capture_ineligible_reason"


def _mark_capture_ineligible(env: Any, reason: str) -> None:
    setattr(env, _CAPTURE_INELIGIBLE_ATTR, str(reason))


def yawn_capture_ineligible_reason(env: Any) -> str | None:
    """Hard pre-compare capture reject from the last ``capture_successor_cell`` call."""
    reason = getattr(env, _CAPTURE_INELIGIBLE_ATTR, None)
    return str(reason) if reason else None


def _clear_capture_ineligible(env: Any) -> None:
    setattr(env, _CAPTURE_INELIGIBLE_ATTR, None)


def _route_item(entry: Any) -> tuple[str, int]:
    if isinstance(entry, dict):
        name = canonical_item(str(entry.get("item") or entry.get("name") or ""))
        return name, max(1, int(entry.get("qty", 1) or 1))
    return canonical_item(str(entry)), 1


def _inventory_id_slots(state: dict[str, Any]) -> list[tuple[int, int]]:
    slots: list[tuple[int, int]] = []
    raw = state.get("inventory_slots")
    if raw is None:
        raw = [(name, 1) for name in (state.get("inventory") or [])]
    for entry in raw:
        if isinstance(entry, dict):
            name = canonical_item(str(entry.get("name") or entry.get("item") or ""))
            qty = int(entry.get("qty", 1) or 0)
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            name = canonical_item(str(entry[0]))
            qty = int(entry[1])
        else:
            continue
        item_id = _ITEM_NAME_TO_ID.get(name)
        if item_id is not None:
            slots.append((item_id, qty))
    return (slots + [(0, 0)] * INVENTORY_SLOTS)[:INVENTORY_SLOTS]


def _occupied_inventory_count(state: dict[str, Any]) -> int:
    raw = state.get("inventory_slots")
    if raw is None:
        return min(INVENTORY_SLOTS, len(state.get("inventory") or []))
    occupied = 0
    for entry in raw:
        if isinstance(entry, dict):
            occupied += bool(entry.get("name") or entry.get("item"))
        elif isinstance(entry, (list, tuple)) and entry:
            occupied += bool(entry[0])
    return min(INVENTORY_SLOTS, int(occupied))


def apply_logistics_feasibility_mask(
    mask: Any,
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
    planner: Any,
) -> Any:
    """Mask only route-feasibility violations; never prescribe combat supplies."""
    from re1_rl.action_mask import (
        N_WITHDRAW_ACTIONS,
        WITHDRAW_ACTION_BASE,
    )
    from re1_rl.item_box import plan_withdraw

    rows: list[dict[str, Any]] = []
    for offset in range(int(getattr(planner, "waypoints_remaining", 0))):
        step = planner.peek_objective(offset)
        room = planner.peek_waypoint_room(offset)
        if step is None or room is None:
            break
        rows.append(step)
        if step.get("action_type") == "fight":
            break
        if offset > 0 and is_box_room(str(room)):
            break
    pressure = peak = 0
    for row in rows:
        pressure -= len(row.get("consume_before_gain", []))
        pressure += len(row.get("items_gained", []))
        peak = max(peak, pressure)
    required_headroom = min(INVENTORY_SLOTS, max(0, peak))
    from re1_rl.yawn_box_prep_checkpoint import yawn_box_forbidden_weapon_ammo_ids

    guns_ammo = yawn_box_forbidden_weapon_ammo_ids()
    for slot in range(min(N_WITHDRAW_ACTIONS, len(box))):
        action = WITHDRAW_ACTION_BASE + slot
        if action >= len(mask) or not mask[action]:
            continue
        # Never block taking guns/ammo back out of the box. Yawn 118 prep
        # cannot succeed with bazooka banked; the headroom guard was leaving
        # only Close legal after Withdraw-open.
        if int(box[slot][0]) & 0xFF in guns_ammo:
            continue
        _new_box, new_inventory, moved = plan_withdraw(inventory, box, slot)
        if moved <= 0:
            continue
        free_after = sum(item_id == 0 for item_id, _qty in new_inventory)
        if free_after < required_headroom:
            mask[action] = False
    return mask


def _consume_inventory_item(
    slots: list[tuple[int, int]], item_id: int, qty: int
) -> bool:
    remaining = max(1, int(qty))
    for i, (slot_id, slot_qty) in enumerate(slots):
        if slot_id != item_id:
            continue
        available = max(1, int(slot_qty))
        used = min(available, remaining)
        remaining -= used
        if used >= available:
            slots[i] = (0, 0)
        else:
            slots[i] = (slot_id, int(slot_qty) - used)
        if remaining == 0:
            return True
    return False


def successor_capacity(
    state: dict[str, Any],
    next_checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe whether this cell can accept the next mandatory checkpoint gains."""
    gains = [_route_item(item) for item in (next_checkpoint or {}).get("items_gained", [])]
    free_slots = max(0, INVENTORY_SLOTS - _occupied_inventory_count(state))
    box_room = is_box_room(str(state.get("room_id", "")))
    metadata: dict[str, Any] = {
        "inventory_free_slots": free_slots,
        "next_checkpoint_id": str((next_checkpoint or {}).get("checkpoint_id") or ""),
        "next_slots_needed": 0,
        "inventory_feasible": True,
        "captured_in_box_room": box_room,
        "logistics_horizon": "next_box_or_boss",
        "route_required_items": sorted(
            canonical_item(str(item))
            for item in (next_checkpoint or {}).get("required_items", [])
        ),
        "declared_gain_count": len(gains),
        "next_is_boss": (next_checkpoint or {}).get("action_type") == "fight",
    }
    if box_room or not gains:
        return metadata

    slots = _inventory_id_slots(state)
    for entry in (next_checkpoint or {}).get("consume_before_gain", []):
        name, qty = _route_item(entry)
        item_id = _ITEM_NAME_TO_ID.get(name)
        if item_id is None or not _consume_inventory_item(slots, item_id, qty):
            metadata["inventory_feasible"] = False
            return metadata

    occupied_before_gains = sum(item_id != 0 for item_id, _ in slots)
    for name, qty in gains:
        item_id = _ITEM_NAME_TO_ID.get(name)
        if item_id is None:
            metadata["inventory_feasible"] = False
            return metadata
        source = [(item_id, qty)]
        source, slots, moved = apply_stack_transfer(source, slots, 0)
        if moved < qty:
            metadata["next_slots_needed"] = max(
                1,
                sum(slot_id != 0 for slot_id, _ in slots)
                - occupied_before_gains
                + 1,
            )
            metadata["inventory_feasible"] = False
            return metadata
    occupied_after_gains = sum(item_id != 0 for item_id, _ in slots)
    metadata["next_slots_needed"] = max(
        0, occupied_after_gains - occupied_before_gains
    )
    return metadata


def _checkpoint_after_row(
    project_root: Path,
    stage: dict[str, Any],
    checkpoint_index: int,
) -> dict[str, Any] | None:
    route_path = stage.get("route_path")
    if not route_path:
        return None
    route = json.loads((project_root / str(route_path)).read_text(encoding="utf-8"))
    next_index = int(checkpoint_index) + 1
    if not isinstance(route, list) or next_index < 0 or next_index >= len(route):
        return None
    checkpoint = route[next_index]
    return checkpoint if isinstance(checkpoint, dict) else None


def _sampling_row_eligible(
    project_root: Path,
    stage: dict[str, Any],
    row: dict[str, Any],
) -> bool:
    try:
        checkpoint_index = int(row["checkpoint_index"])
    except (KeyError, TypeError, ValueError):
        return False
    # Terminal captures (cp96 / empty next id) have nothing left to hunt.
    if "next_checkpoint_id" in row and not str(row.get("next_checkpoint_id") or "").strip():
        return False
    next_checkpoint = _checkpoint_after_row(project_root, stage, checkpoint_index)
    if next_checkpoint is None and stage.get("route_path"):
        try:
            route = json.loads(
                (project_root / str(stage["route_path"])).read_text(encoding="utf-8")
            )
            route_n = len(route) if isinstance(route, list) else 0
        except (OSError, json.JSONDecodeError, TypeError):
            route_n = 0
        if route_n > 0 and checkpoint_index == route_n - 1:
            return False
    mandatory_pickup = bool((next_checkpoint or {}).get("items_gained"))
    if not mandatory_pickup:
        return True
    # Mandatory-pickup legs fail closed for legacy/unverifiable rows.
    return (
        row.get("inventory_feasible") is True
        and "inventory_free_slots" in row
        and "next_slots_needed" in row
        and "captured_in_box_room" in row
    )


def load_manifest(project_root: Path, stage: dict[str, Any]) -> dict[str, Any]:
    path = project_root / stage["cells_manifest"]
    if not path.is_file():
        return {"schema_version": 1, "route_id": stage.get("route_id"), "cells": []}
    # utf-8-sig: PowerShell Set-Content -Encoding UTF8 writes a BOM that
    # plain utf-8 json.loads rejects (crashes workers on empty-manifest wipes).
    # Windows multi-actor races on manifest replace → PermissionError; retry.
    from re1_rl.win_fs_retry import read_text_retry

    data = json.loads(read_text_retry(path, encoding="utf-8-sig"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cells"), list):
        raise ValueError(f"invalid Yawn rails cells manifest: {path}")
    if data.get("route_id") != stage.get("route_id"):
        raise ValueError(
            f"Yawn rails manifest route_id mismatch: {path}"
        )
    return data


def iter_loadable_cells(
    project_root: Path,
    stage: dict[str, Any],
) -> list[dict[str, Any]]:
    """Eligible atomic reset rows with on-disk state + sidecar."""
    root = Path(project_root)
    out: list[dict[str, Any]] = []
    for row in load_manifest(root, stage)["cells"]:
        state = root / str(row.get("state_path", ""))
        sidecar = root / str(row.get("sidecar_path", ""))
        if (
            state.is_file()
            and sidecar.is_file()
            and _sampling_row_eligible(root, stage, row)
        ):
            out.append(dict(row))
    return sorted(out, key=lambda r: int(r["checkpoint_index"]))


def validate_manifest_cells(
    project_root: Path,
    stage: dict[str, Any],
    *,
    require_contiguous_prefix: int = 5,
) -> list[str]:
    """Smoke-check manifest wiring / early-route contiguity (no BizHawk needed)."""
    root = Path(project_root)
    errors: list[str] = []
    try:
        manifest = load_manifest(root, stage)
    except ValueError as exc:
        return [str(exc)]
    cells = [
        row for row in manifest.get("cells", []) if isinstance(row, dict)
    ]
    by_idx: dict[int, dict[str, Any]] = {}
    for row in cells:
        try:
            idx = int(row["checkpoint_index"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"cell missing checkpoint_index: {row!r}")
            continue
        if idx in by_idx:
            errors.append(f"duplicate checkpoint_index {idx}")
        by_idx[idx] = row
        state = root / str(row.get("state_path", ""))
        sidecar = root / str(row.get("sidecar_path", ""))
        if not state.is_file():
            errors.append(f"cp{idx:02d}: missing state {state}")
        elif state.stat().st_size < 50_000:
            errors.append(f"cp{idx:02d}: state suspiciously small ({state.stat().st_size} bytes)")
        if not sidecar.is_file():
            errors.append(f"cp{idx:02d}: missing sidecar {sidecar}")
    prefix = int(require_contiguous_prefix)
    if prefix > 0:
        missing = [i for i in range(prefix) if i not in by_idx]
        if missing:
            errors.append(
                "early route not contiguous; missing "
                + ", ".join(f"cp{i:02d}" for i in missing)
            )
    return errors


# Non-PLR reset mix: 50% frontier cell; remaining 50% uniform over any
# eligible cp18+ cell (including latest). Conditions before cp18 are frozen.
RESET_MIN_CHECKPOINT_INDEX = 18
RESET_LATEST_CELL_WEIGHT = 0.50
_RESET_LATEST_ONLY_ENV = "RE1_YAWN_RESET_LATEST_ONLY"
_RESET_PIN_INDEX_ENV = "RE1_YAWN_RESET_PIN_INDEX"
_RESET_PIN_RANGE_ENV = "RE1_YAWN_RESET_PIN_RANGE"
_RESET_PIN_SET_ENV = "RE1_YAWN_RESET_PIN_SET"
_RESET_PIN_SET_WEIGHT_ENV = "RE1_YAWN_RESET_PIN_SET_WEIGHT"
_RESET_PIN_WEIGHTS_ENV = "RE1_YAWN_RESET_PIN_WEIGHTS"
_RESET_PIN_FILE_ENV = "RE1_YAWN_RESET_PIN_FILE"
_DEFAULT_PIN_FILE = "data/yawn_reset_pin.env"
_RESET_FRONTIER_FIGHT_ENV = "RE1_YAWN_RESET_FRONTIER_FIGHT_ONLY"
PIN_WEIGHT_LATEST_KEY = "latest"
_LATEST_PIN_ALIASES = frozenset({"latest", "newest", "front", "frontier"})


def _pin_file_path(project_root: Path | str | None = None) -> Path | None:
    """Optional hot-reload pin file (read every reset; no worker restart).

    Relative paths resolve against ``project_root`` (repo root), not process cwd.
    Actors can spawn with a different cwd; missing the overlay used to fall
    through to the launcher pin silently. Blank ``RE1_YAWN_RESET_PIN_FILE``
    (including whitespace) uses ``data/yawn_reset_pin.env``.
    """
    raw = os.environ.get(_RESET_PIN_FILE_ENV, _DEFAULT_PIN_FILE).strip() or _DEFAULT_PIN_FILE
    path = Path(raw)
    if not path.is_absolute():
        base = Path(project_root) if project_root is not None else Path.cwd()
        path = base / path
    return path if path.is_file() else None


def _parse_pin_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _pin_env_raw(
    key: str, project_root: Path | str | None = None
) -> str | None:
    """Pin knob: file override wins when key is present; else launcher env."""
    pin_file = _pin_file_path(project_root)
    if pin_file is not None:
        overrides = _parse_pin_file(pin_file)
        if key in overrides:
            raw = overrides[key]
            return raw if raw else None
    raw = os.environ.get(key, "").strip()
    return raw if raw else None


def reset_latest_only_from_env() -> bool:
    """``RE1_YAWN_RESET_LATEST_ONLY=1`` — always start from the newest loadable cell."""
    raw = os.environ.get(_RESET_LATEST_ONLY_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def reset_pin_index_from_env(
    project_root: Path | str | None = None,
) -> int | None:
    """``RE1_YAWN_RESET_PIN_INDEX=N`` — force every reset onto curated cell ``cpNN``.

    Loads ``cpN`` so the next success captures ``cp{N+1}`` (e.g. pin 33 → hunt cp34).
    """
    raw = _pin_env_raw(_RESET_PIN_INDEX_ENV, project_root)
    if not raw:
        return None
    try:
        idx = int(raw, 10)
    except ValueError:
        return None
    if idx < 0:
        return None
    return idx


def reset_pin_range_from_env(
    project_root: Path | str | None = None,
) -> tuple[int, int] | None:
    """``RE1_YAWN_RESET_PIN_RANGE=27-37`` — uniform resets over loadable cells in range.

    Inclusive lo/hi. Accepted forms: ``27-37``, ``27:37``, ``27,37``.
    Single-index pin (``RE1_YAWN_RESET_PIN_INDEX``) still wins when both are set.
    """
    raw = _pin_env_raw(_RESET_PIN_RANGE_ENV, project_root)
    if not raw:
        return None
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


def reset_pin_set_from_env(
    project_root: Path | str | None = None,
) -> tuple[frozenset[int], float] | None:
    """``RE1_YAWN_RESET_PIN_SET=37,40,44`` — optional weighted pin-set blend.

    With ``RE1_YAWN_RESET_PIN_SET_WEIGHT=0.5`` (default), that fraction of resets
    sample uniformly from the listed indices; the remainder uses the normal mix.
    Single-index and range pins still override when set.
    """
    raw = _pin_env_raw(_RESET_PIN_SET_ENV, project_root)
    if not raw:
        return None
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
    if not indices:
        return None
    weight_raw = _pin_env_raw(_RESET_PIN_SET_WEIGHT_ENV, project_root) or "0.5"
    try:
        weight = float(weight_raw)
    except ValueError:
        weight = 0.5
    return frozenset(indices), max(0.0, min(1.0, weight))


def parse_pin_weights(raw: str) -> dict[int | str, float] | None:
    """Parse weighted reset mix.

    Fixed cells: ``33:20,36:30,40:50`` or ``33=0.2,36=0.3``.
    Dynamic latest: ``latest:50,33:50`` (newest loadable cp18+ cell each reset).
    """
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
        left_token = left.strip()
        try:
            weight = float(right.strip().rstrip("%"))
        except ValueError:
            continue
        if weight <= 0.0:
            continue
        if left_token.lower() in _LATEST_PIN_ALIASES:
            key: int | str = PIN_WEIGHT_LATEST_KEY
        else:
            try:
                idx = int(left_token, 10)
            except ValueError:
                continue
            if idx < 0:
                continue
            key = idx
        weights[key] = weights.get(key, 0.0) + weight
    if not weights:
        return None
    total = sum(weights.values())
    if total <= 0.0:
        return None
    return {key: weight / total for key, weight in weights.items()}


def reset_pin_weights_from_env(
    project_root: Path | str | None = None,
) -> dict[int | str, float] | None:
    """``RE1_YAWN_RESET_PIN_WEIGHTS=latest:50,33:50`` — per-cell reset mix.

    Percentages and fractions are both accepted; values are normalized to sum to 1.
    ``latest`` tracks the newest loadable cp18+ cell and updates as captures land.
    Missing fixed cells are dropped and the remainder is renormalized.
    """
    raw = _pin_env_raw(_RESET_PIN_WEIGHTS_ENV, project_root)
    if not raw:
        return None
    return parse_pin_weights(raw)


def reset_frontier_fight_only_from_env() -> bool:
    """``RE1_YAWN_RESET_FRONTIER_FIGHT_ONLY=1`` — memlog grind on fight frontier."""
    raw = os.environ.get(_RESET_FRONTIER_FIGHT_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def parse_latest_range_mix_weight(raw: str) -> float | None:
    """Parse ``latest:50`` (or ``latest:0.5``) for pin-range + uniform mix.

    Optional trailing ``uniform`` / ``rest`` / ``range`` is ignored.
    Returns ``None`` when ``raw`` names explicit cell indices alongside ``latest``.
    """
    stripped = raw.strip().lower().replace(" ", "")
    if not stripped.startswith("latest"):
        return None
    parts = [p for p in stripped.replace(";", ",").split(",") if p]
    if not parts:
        return None
    if len(parts) > 2:
        return None
    if len(parts) == 2 and parts[1] not in {"uniform", "rest", "range"}:
        return None
    token = parts[0]
    for sep in (":", "=", "/"):
        if sep in token:
            left, right = token.split(sep, 1)
            break
    else:
        return None
    if left.strip() not in _LATEST_PIN_ALIASES:
        return None
    try:
        weight = float(right.strip().rstrip("%"))
    except ValueError:
        return None
    if weight <= 0.0:
        return None
    if right.strip().endswith("%") or weight > 1.0:
        weight /= 100.0
    return max(0.0, min(1.0, weight))


def _sample_cell_from_pin_range_latest_mix(
    cells: list[dict[str, Any]],
    lo: int,
    hi: int,
    latest_weight: float,
    *,
    rng: random.Random,
) -> dict[str, Any]:
    """``latest_weight`` on newest loadable cell in ``[lo, hi]``; else uniform in range."""
    ranged = _cells_in_pin_range(cells, lo, hi)
    if not ranged:
        raise ValueError(
            f"RE1_YAWN_RESET_PIN_RANGE={lo}-{hi} but no loadable cells in range"
        )
    latest = ranged[-1]
    if rng.random() < float(latest_weight):
        return latest
    return ranged[rng.randrange(len(ranged))]


def _cells_in_pin_range(
    cells: list[dict[str, Any]],
    lo: int,
    hi: int,
) -> list[dict[str, Any]]:
    return [
        row
        for row in cells
        if lo <= int(row.get("checkpoint_index", -1)) <= hi
    ]


def _latest_reset_cell_index(cells: list[dict[str, Any]]) -> int | None:
    """Newest loadable cell in the default reset pool (cp18+)."""
    eligible = eligible_reset_cells(cells)
    if eligible:
        return int(eligible[-1]["checkpoint_index"])
    if cells:
        return int(cells[-1]["checkpoint_index"])
    return None


def _resolve_pin_weights(
    cells: list[dict[str, Any]],
    weights: dict[int | str, float],
) -> dict[int, float]:
    latest_idx = _latest_reset_cell_index(cells)
    resolved: dict[int, float] = {}
    for key, weight in weights.items():
        if key == PIN_WEIGHT_LATEST_KEY:
            if latest_idx is None:
                continue
            idx = latest_idx
        else:
            idx = int(key)
        resolved[idx] = resolved.get(idx, 0.0) + weight
    return resolved


def _cells_in_pin_set(
    cells: list[dict[str, Any]],
    indices: frozenset[int] | set[int],
) -> list[dict[str, Any]]:
    want = {int(x) for x in indices}
    return [
        row
        for row in cells
        if int(row.get("checkpoint_index", -1)) in want
    ]


def _sample_cell_from_pin_weights(
    cells: list[dict[str, Any]],
    weights: dict[int | str, float],
    *,
    rng: random.Random,
) -> dict[str, Any]:
    by_index = {
        int(row.get("checkpoint_index", -1)): row
        for row in cells
    }
    resolved = _resolve_pin_weights(cells, weights)
    available: dict[int, float] = {}
    for idx, weight in resolved.items():
        if idx in by_index:
            available[idx] = available.get(idx, 0.0) + weight
    if not available:
        raise ValueError(
            f"RE1_YAWN_RESET_PIN_WEIGHTS lists {sorted(weights)!r} but none are loadable"
        )
    total = sum(available.values())
    indices = sorted(available)
    probs = [available[idx] / total for idx in indices]
    chosen_idx = rng.choices(indices, weights=probs, k=1)[0]
    return by_index[chosen_idx]


def eligible_reset_cells(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cells the random reset sampler may use (cp18+ only)."""
    return [
        row
        for row in cells
        if int(row.get("checkpoint_index", -1)) >= RESET_MIN_CHECKPOINT_INDEX
    ]


def _choose_reset_candidate(
    cells: list[dict[str, Any]],
    *,
    rng: random.Random,
    latest_only: bool = False,
) -> dict[str, Any]:
    """50% latest eligible cell; else uniform over any eligible cp18+ cell."""
    eligible = eligible_reset_cells(cells)
    if not eligible:
        raise ValueError(
            f"no loadable Yawn rails cells at checkpoint_index>={RESET_MIN_CHECKPOINT_INDEX}"
        )
    latest = eligible[-1]
    if latest_only:
        return latest
    if rng.random() < float(RESET_LATEST_CELL_WEIGHT):
        return latest
    return eligible[rng.randrange(len(eligible))]


def _options_from_cell(
    chosen: dict[str, Any],
    stage: dict[str, Any],
    *,
    reset_source: str = "route_cell",
) -> dict[str, Any]:
    start_index = int(chosen["checkpoint_index"]) + 1
    route_steps = list(stage.get("route_steps", []))
    remaining = max(1, len(route_steps) - start_index)
    # Global legs_per_episode remains a hard cap for non-PLR mode; PLR widens
    # per endpoint instead of jumping the whole curriculum to 6-leg episodes.
    leg_span = min(max(1, int(stage.get("legs_per_episode", 1))), remaining)
    opts: dict[str, Any] = {
        "route_start_index": start_index,
        "leg_span": leg_span,
        "reset_source": reset_source if start_index else "route_initial",
    }
    if start_index:
        opts["pb_bundle"] = {
            "state_path": str(chosen["state_path"]),
            "sidecar_path": str(chosen["sidecar_path"]),
            "source": "yawn_rails",
        }
    return opts


def sample_one_leg_options(
    project_root: Path,
    stage: dict[str, Any],
    *,
    rng: random.Random,
) -> dict[str, Any]:
    """Choose a curated start and bounded checkpoint span.

    Default: 50% latest; 50% uniform over any loadable cp18+ cell.
    ``RE1_YAWN_PAYFORWARD_RIPPLE=1`` enables fight-progression mix instead:
    40% frontier fight cell, 60% uniform over all loadable cells from cp00.
    ``RE1_YAWN_RESET_LATEST_ONLY=1`` forces the newest cell.
    ``RE1_YAWN_RESET_FRONTIER_FIGHT_ONLY=1`` forces the fight-progression frontier.
    ``RE1_YAWN_RESET_PIN_INDEX=N`` forces curated cell ``cpNN`` (overrides mix).
    ``RE1_YAWN_RESET_PIN_RANGE=LO-HI`` uniform over loadable cells in that range
    (overridden by a single pin index when both are set).
    ``RE1_YAWN_RESET_PIN_WEIGHTS=latest:50`` with a pin range: ``latest_weight`` on
    the newest loadable cell in range, remainder uniform over that range (same as
    the default cp18+ mix but floored at ``LO``).
    Other ``RE1_YAWN_RESET_PIN_WEIGHTS`` entries sample by explicit per-cell
    weights (percentages or fractions; normalized). ``latest:50,33:50`` mixes the
    newest loadable cp18+ cell with fixed indices. Overrides plain pin range/set.
    ``RE1_YAWN_RESET_PIN_SET=37,40,44`` with optional
    ``RE1_YAWN_RESET_PIN_SET_WEIGHT=0.5`` blends pin-set vs normal mix.
    ``RE1_YAWN_FIGHT_BIAS_INDEX`` / ``RE1_YAWN_FIGHT_BIAS_WEIGHT`` override the
    payforward fight cell and its share (workhorse grind without fleet restart).
    ``data/yawn_reset_pin.env`` (or ``RE1_YAWN_RESET_PIN_FILE``) overrides the
    above on every reset without restarting the worker — edit the file live.
    Relative pin paths are resolved from ``project_root``, not process cwd.
    """
    all_cells = list(iter_loadable_cells(project_root, stage))
    cells = eligible_reset_cells(all_cells)
    pin_index = reset_pin_index_from_env(project_root)
    if pin_index is not None:
        pinned = [
            row
            for row in cells
            if int(row.get("checkpoint_index", -1)) == int(pin_index)
        ]
        if not pinned:
            # Pin may target a curated cell below the cp18 reset floor.
            pinned = [
                row
                for row in iter_loadable_cells(project_root, stage)
                if int(row.get("checkpoint_index", -1)) == int(pin_index)
            ]
        if not pinned:
            raise ValueError(
                f"RE1_YAWN_RESET_PIN_INDEX={pin_index} but cp{int(pin_index):02d} "
                "is not loadable"
            )
        return _options_from_cell(pinned[0], stage, reset_source="route_cell_pin")
    pin_range = reset_pin_range_from_env(project_root)
    raw_pin_weights = _pin_env_raw(_RESET_PIN_WEIGHTS_ENV, project_root)
    if pin_range is not None and raw_pin_weights:
        latest_range_w = parse_latest_range_mix_weight(raw_pin_weights)
        if latest_range_w is not None:
            lo, hi = pin_range
            chosen = _sample_cell_from_pin_range_latest_mix(
                all_cells, lo, hi, latest_range_w, rng=rng
            )
            return _options_from_cell(
                chosen, stage, reset_source="route_cell_pin_range_latest"
            )
    pin_weights = reset_pin_weights_from_env(project_root)
    if pin_weights is not None:
        chosen = _sample_cell_from_pin_weights(all_cells, pin_weights, rng=rng)
        return _options_from_cell(chosen, stage, reset_source="route_cell_pin_weights")
    if pin_range is not None:
        lo, hi = pin_range
        ranged = _cells_in_pin_range(all_cells, lo, hi)
        if not ranged:
            raise ValueError(
                f"RE1_YAWN_RESET_PIN_RANGE={lo}-{hi} but no loadable cells in range"
            )
        chosen = ranged[rng.randrange(len(ranged))]
        return _options_from_cell(chosen, stage, reset_source="route_cell_pin_range")
    pin_set_cfg = reset_pin_set_from_env(project_root)
    if pin_set_cfg is not None:
        indices, weight = pin_set_cfg
        if weight > 0.0 and rng.random() < weight:
            pinned = _cells_in_pin_set(all_cells, indices)
            if pinned:
                chosen = pinned[rng.randrange(len(pinned))]
                return _options_from_cell(
                    chosen, stage, reset_source="route_cell_pin_set"
                )
    if reset_frontier_fight_only_from_env():
        from re1_rl.yawn_rails_payforward import sample_frontier_fight_options

        opts = sample_frontier_fight_options(project_root, stage, all_cells)
        if opts is not None:
            return opts
    latest_only = reset_latest_only_from_env()
    from re1_rl.yawn_rails_plr import plr_enabled_from_env, sample_plr_options

    if plr_enabled_from_env() and not latest_only:
        candidates = list(cells)
        return sample_plr_options(project_root, stage, candidates, rng=rng)
    if not latest_only:
        from re1_rl.yawn_rails_payforward import sample_payforward_options

        pf = sample_payforward_options(project_root, stage, all_cells, rng=rng)
        if pf is not None:
            return pf
    chosen = _choose_reset_candidate(cells, rng=rng, latest_only=latest_only)
    return _options_from_cell(chosen, stage)


def validate_route(
    route: list[dict[str, Any]],
    *,
    graph: Any,
) -> list[str]:
    """Return auditable route defects; empty means the route contract is usable."""
    errors: list[str] = []
    if not route:
        return ["route is empty"]
    seen_ids: set[str] = set()
    for i, checkpoint in enumerate(route):
        cid = str(checkpoint.get("checkpoint_id", ""))
        room = str(checkpoint.get("room_id", ""))
        if checkpoint.get("seq") != i + 1:
            errors.append(f"{cid or i}: seq must be contiguous and 1-based")
        if not cid or cid in seen_ids:
            errors.append(f"checkpoint[{i}] has missing/duplicate checkpoint_id {cid!r}")
        seen_ids.add(cid)
        if not room:
            errors.append(f"{cid or i}: missing room_id")
        condition = checkpoint.get("success_condition")
        if not isinstance(condition, dict):
            errors.append(f"{cid or i}: success_condition must be an object")
        else:
            condition_text = json.dumps(condition, sort_keys=True)
            for item in checkpoint.get("items_gained", []):
                item_name, _qty = _route_item(item)
                if item_name not in _ITEM_NAME_TO_ID:
                    errors.append(f"{cid or i}: gained item {item_name!r} is not inventory-mappable")
                if f'"item": "{item_name}"' not in condition_text:
                    errors.append(
                        f"{cid or i}: gained item {item_name!r} is not required by success"
                    )
            for item in checkpoint.get("consume_before_gain", []):
                item_name, _qty = _route_item(item)
                if item_name not in _ITEM_NAME_TO_ID:
                    errors.append(
                        f"{cid or i}: consumed item {item_name!r} is not inventory-mappable"
                    )
        text = json.dumps(checkpoint, sort_keys=True).lower()
        if "serum" in text:
            errors.append(f"{cid or i}: serum is forbidden in the Yawn route")
        if "broken_shotgun" in text:
            errors.append(f"{cid or i}: broken_shotgun is forbidden in the Yawn route")
        if room == "205":
            errors.append(f"{cid or i}: room 205 is not on the approved route")
        if i:
            prev_room = str(route[i - 1].get("room_id", ""))
            if prev_room != room and graph.hop_distance(prev_room, room) is None:
                errors.append(f"{route[i - 1].get('checkpoint_id')}->{cid}: no legal room path")
    return errors


def _settle_state_for_capture(env: Any, state: dict[str, Any]) -> dict[str, Any] | None:
    """Return an in-control state for successor capture, or None.

    Checkpoint success often fires mid-cinema or with the post-pickup ITEM /
    Yes-No pause still open. Episodes also terminate on that reward, so the
    next PPO step never gets to auto-dismiss — settle in-place first.
    """
    if state.get("dead"):
        return None
    if state.get("in_control", True):
        return state
    read_state = getattr(env, "_read_state", None)
    if not callable(read_state):
        print(
            "[yawn_capture] reject not in_control (no settle helper)",
            flush=True,
        )
        return None
    print(
        "[yawn_capture] settle before capture "
        f"room={state.get('room_id')!r}",
        flush=True,
    )

    # 1) Accept "Will you take …?" — turbo-skip refuses pause menus.
    accept = getattr(env, "_auto_accept_pause_pickup_modal", None)
    if callable(accept):
        try:
            accepted = bool(accept())
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            print(f"[yawn_capture] settle accept err={exc!r}", flush=True)
            accepted = False
        if accepted:
            print("[yawn_capture] settle accepted pickup Yes/No", flush=True)

    # 2) Triangle-close leftover ITEM/STATUS (also refuses turbo-skip).
    dismiss = getattr(env, "_try_dismiss_orphan_item_menu", None)
    if callable(dismiss):
        try:
            recovered, report = dismiss()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            print(f"[yawn_capture] settle dismiss err={exc!r}", flush=True)
            recovered, report = False, {"error": repr(exc)}
        print(
            f"[yawn_capture] settle item_menu dismissed={bool(recovered)} "
            f"report={report}",
            flush=True,
        )

    # 3) Door / cinema turbo-skip once pause tree is clear.
    skip = getattr(env, "_skip_uncontrolled", None)
    if callable(skip):
        try:
            _skipped, died = skip()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            print(f"[yawn_capture] reject settle skip failed err={exc!r}", flush=True)
            return None
        if died:
            print("[yawn_capture] reject died during settle", flush=True)
            return None

    try:
        settled = dict(read_state(track_items=False))
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
        print(f"[yawn_capture] reject settle re-read failed err={exc!r}", flush=True)
        return None

    # One more dismiss pass if skip left an ITEM overlay.
    if (
        not settled.get("dead")
        and not settled.get("in_control", True)
        and callable(dismiss)
    ):
        try:
            recovered, report = dismiss()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
            recovered, report = False, {}
        print(
            f"[yawn_capture] settle item_menu retry dismissed={bool(recovered)} "
            f"report={report}",
            flush=True,
        )
        try:
            settled = dict(read_state(track_items=False))
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            print(
                f"[yawn_capture] reject settle re-read failed err={exc!r}",
                flush=True,
            )
            return None

    if settled.get("dead") or not settled.get("in_control", True):
        print(
            "[yawn_capture] reject still unsettled after settle "
            f"in_control={settled.get('in_control')} dead={settled.get('dead')}",
            flush=True,
        )
        return None
    return settled


def _log_capture_quality_vs_incumbent(
    project_root: Path,
    *,
    checkpoint_index: int,
    checkpoint_id: str,
    quality: list[int] | tuple[int, ...],
    note: str = "",
) -> None:
    """Print lexicographic quality vs curated cell (pay-forward gate preview)."""
    from re1_rl.go_explore_archive import quality_beats
    from re1_rl.go_explore_capture import quality_replace_significant
    from re1_rl.yawn_rails_sync import _existing_cell_quality, yawn_rails_root

    new_q = [int(x) for x in quality]
    old_q = _existing_cell_quality(yawn_rails_root(project_root), checkpoint_index)
    if old_q is None:
        verdict = "NEW_SLOT"
        beats = True
        significant = True
        old_list: list[int] | None = None
    else:
        old_list = [int(x) for x in old_q]
        beats = bool(quality_beats(tuple(new_q), old_q))
        significant = bool(quality_replace_significant(tuple(new_q), old_q))
        if beats and significant:
            verdict = "WOULD_INSTALL"
        elif beats and not significant:
            verdict = "BEATS_NOT_SIGNIFICANT"
        else:
            verdict = "LOSE_TO_INCUMBENT"
    suffix = f" note={note}" if note else ""
    print(
        f"[yawn_capture] quality cp{int(checkpoint_index):02d} "
        f"id={checkpoint_id} new={new_q} old={old_list} "
        f"beats={beats} significant={significant} payforward={verdict}{suffix}",
        flush=True,
    )


def capture_successor_cell(
    env: Any,
    state: dict[str, Any],
    breakdown: dict[str, float],
) -> dict[str, Any] | None:
    """Capture a clean successor start and return a fleet sync proposal.

    Always saves into a staging directory first (never blind-overwrites curated
    ``cpNN``). When ``RE1_YAWN_RAILS_SYNC=1`` (fleet default), only the proposal
    is returned — the learner quality-gate admits, then workers install via
    verified poll. When sync is off, local compare-and-swap installs only if the
    new quality beats the existing curated cell.
    """
    if float(breakdown.get("checkpoint_success", 0.0)) <= 0.0:
        return None
    _clear_capture_ineligible(env)
    stage = getattr(env, "_stage", {})
    if stage.get("mode") != "yawn_rails":
        return None
    planner = env._planner
    completed = int(planner.waypoint_index) - 1
    if completed < 0 or completed >= planner.total_waypoints:
        return None
    completed_cp = planner.step_by_seq(completed + 1) or {}
    cid = str(completed_cp.get("checkpoint_id", "") or "")
    expected_room = str(completed_cp.get("room_id", "") or "")
    unsettled_state = state
    state = _settle_state_for_capture(env, state)
    if state is None:
        try:
            from re1_rl.go_explore_capture import compute_quality

            q = compute_quality(
                unsettled_state,
                ever_held=getattr(getattr(env, "_items", None), "ever_held", None),
                env=env,
            )
            _log_capture_quality_vs_incumbent(
                Path(env.project_root),
                checkpoint_index=completed,
                checkpoint_id=cid or f"idx{completed}",
                quality=q,
                note="unsettled_reject",
            )
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
            pass
        _mark_capture_ineligible(env, "unsettled")
        return None
    room_id = str(state.get("room_id", "") or "")
    # Refuse door-threshold / cutscene-spoof captures (Wesker→fake 106, Kenneth
    # at 105→104 entry pose before tea-room settle, etc.).
    from re1_rl.barry_rescue_checkpoint import barry_rescue_capture_room_ok
    from re1_rl.richard_cutscene_checkpoint import richard_cutscene_capture_room_ok
    from re1_rl.yawn_box_prep_checkpoint import yawn_box_prep_capture_room_ok

    def _scripted_exit_capture_ok(
        completed_cid: str, live_room: str, expect_room: str
    ) -> bool:
        return barry_rescue_capture_room_ok(
            completed_cid, live_room, expect_room
        ) or richard_cutscene_capture_room_ok(
            completed_cid, live_room, expect_room
        ) or yawn_box_prep_capture_room_ok(
            completed_cid, live_room, expect_room
        )

    if expected_room and room_id.upper() != expected_room.upper():
        if not _scripted_exit_capture_ok(cid, room_id, expected_room):
            _mark_capture_ineligible(env, "room_mismatch")
            return None
    progress = getattr(env, "_progress", None)
    ledgers: set[str] = set()
    if progress is not None:
        ledgers = set(progress.observed_cutscenes or ()) | set(
            progress.rewarded_cutscenes or ()
        )
    if cid == "barry_rescue_115" and not any(
        str(k).startswith("115:") for k in ledgers
    ):
        _mark_capture_ineligible(env, "cutscene_gate")
        return None
    if cid == "richard_cutscene_20D" and not any(
        str(k).startswith("20D:") for k in ledgers
    ):
        _mark_capture_ineligible(env, "cutscene_gate")
        return None
    if cid == "kenneth_104" and not any(str(k).startswith("104:") for k in ledgers):
        _mark_capture_ineligible(env, "cutscene_gate")
        return None
    if cid == "barry_return_105" and not any(
        str(k).startswith("105:2:s1") for k in ledgers
    ):
        _mark_capture_ineligible(env, "cutscene_gate")
        return None
    if cid == "main_hall_106" and not any(str(k).startswith("106:") for k in ledgers):
        _mark_capture_ineligible(env, "cutscene_gate")
        return None
    if cid in (
        "crow_gallery_enter_117",
        "crest_gate_11A",
        "upper_hall_enter_203",
        "storeroom_enter_118",
    ) and progress is not None:
        # claim_checkpoint_success clears live kills before capture runs; use
        # the claim-time snapshot (falls back to live for unit tests).
        leg_kill_gates = {
            "crow_gallery_enter_117": ("10A", 2),
            "crest_gate_11A": ("11A", 1),
            "upper_hall_enter_203": ("204", 2),
            "storeroom_enter_118": ("10B", 1),
        }
        room_key, need = leg_kill_gates[cid]
        kills_map = progress.leg_kills_for_capture()
        leg_kills = int(kills_map.get(room_key, 0))
        if leg_kills < need:
            print(
                f"[yawn_capture] reject leg_kills_{room_key}={leg_kills} "
                f"need={need} cp={cid}",
                flush=True,
            )
            _mark_capture_ineligible(env, "leg_kills")
            return None
        progress.restore_claimed_leg_kills_for_sidecar()
    # First climb to 203 has no cinema at this story beat — do not require 203:.
    next_checkpoint = planner.step_by_seq(completed + 2)
    capacity = successor_capacity(state, next_checkpoint)
    if not capacity["inventory_feasible"]:
        print(
            f"[yawn_capture] reject inventory_feasible=False cp={cid} "
            f"free={capacity.get('inventory_free_slots')} "
            f"need={capacity.get('next_slots_needed')}",
            flush=True,
        )
        _mark_capture_ineligible(env, "inventory_infeasible")
        return None
    min_free = CAPTURE_MIN_FREE_SLOTS.get(cid)
    if min_free is not None:
        free_slots = int(capacity.get("inventory_free_slots", 0))
        if free_slots < int(min_free):
            print(
                f"[yawn_capture] reject inventory_free_slots={free_slots} "
                f"need={int(min_free)} cp={cid}",
                flush=True,
            )
            _mark_capture_ineligible(env, "inventory_free_slots")
            return None

    inv_names = {
        canonical_item(str(x))
        for x in (state.get("inventory") or [])
        if str(x).strip()
    }
    for item in completed_cp.get("consume_before_gain") or []:
        name = canonical_item(str(item))
        if name and name in inv_names:
            print(
                f"[yawn_capture] reject consume_before_gain still held "
                f"item={name!r} cp={cid}",
                flush=True,
            )
            _mark_capture_ineligible(env, "consume_before_gain")
            return None

    from re1_rl.go_explore_capture import compute_quality
    from re1_rl.yawn_rails_sync import (
        CELL_META_NAME,
        CELL_SIDECAR_NAME,
        CELL_STATE_NAME,
        build_capture_proposal,
        cell_dir_name,
        try_install_yawn_cell,
        yawn_rails_root,
        yawn_rails_sync_enabled,
    )

    root = Path(env.project_root)
    yr = yawn_rails_root(root)
    # Unique per capture — never share staging across concurrent envs/pids.
    staging = (
        yr
        / ".staging"
        / f"{cell_dir_name(completed)}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    )
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    state_path = staging / CELL_STATE_NAME
    sidecar_path = staging / CELL_SIDECAR_NAME
    # Freeze async cutscene-skip so bg fast_forward cannot advance the emu
    # between the success RAM snapshot and savestate.save (sidecar/State skew).
    # _macro_active blocks new chunks; _bg_skip_emu_lock waits out an in-flight
    # chunk already inside skip_uncontrolled.
    prev_macro = bool(getattr(env, "_macro_active", False))
    env._macro_active = True
    emu_lock = getattr(env, "_bg_skip_emu_lock", None)
    try:
        live_room = room_id
        live_state = state
        read_state = getattr(env, "_read_state", None)

        def _locked_section():
            nonlocal live_room, live_state
            if callable(read_state):
                try:
                    live = read_state(track_items=False)
                    live_room = str(live.get("room_id", "") or "")
                    live_state = live
                except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
                    live_room = room_id
            if expected_room and live_room.upper() != expected_room.upper():
                if not _scripted_exit_capture_ok(cid, live_room, expected_room):
                    print(
                        f"[yawn_capture] reject pre-save room drift "
                        f"live={live_room!r} expected={expected_room!r} cp={cid}",
                        flush=True,
                    )
                    _mark_capture_ineligible(env, "room_drift")
                    return False
            if not live_state.get("in_control", True) or live_state.get("dead"):
                print(
                    f"[yawn_capture] reject pre-save not settled "
                    f"in_control={live_state.get('in_control')} "
                    f"dead={live_state.get('dead')} cp={cid}",
                    flush=True,
                )
                _mark_capture_ineligible(env, "unsettled")
                return False
            env.bridge.save_savestate(str(state_path))
            if callable(read_state):
                try:
                    live_after = read_state(track_items=False)
                    after_room = str(live_after.get("room_id", "") or "")
                    live_state = live_after
                except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
                    print(
                        f"[yawn_capture] reject post-save read failure "
                        f"cp={cid} err={exc!r}",
                        flush=True,
                    )
                    _mark_capture_ineligible(env, "post_save_read")
                    return False
                if expected_room and after_room.upper() != expected_room.upper():
                    if not _scripted_exit_capture_ok(
                        cid, after_room, expected_room
                    ):
                        print(
                            f"[yawn_capture] reject post-save room drift "
                            f"after={after_room!r} expected={expected_room!r} cp={cid}",
                            flush=True,
                        )
                        _mark_capture_ineligible(env, "room_drift")
                        return False
                live_room = after_room
            return True

        if emu_lock is not None:
            with emu_lock:
                if not _locked_section():
                    return None
        else:
            if not _locked_section():
                return None
        from re1_rl.item_box import box_pollution_reason, read_box_live

        try:
            live_box = read_box_live(env.bridge)
            setattr(env, "_box_cache", live_box)
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
            live_box = getattr(env, "_box_cache", None)
        pollution = box_pollution_reason(live_box)
        if cid == "yawn_box_prep_118":
            from re1_rl.yawn_box_prep_checkpoint import yawn_box_prep_capture_ready

            pollution = yawn_box_prep_capture_ready(
                live_box,
                list(live_state.get("inventory") or []),
            )
        if pollution:
            print(
                f"[yawn_capture] reject box pollution {pollution} cp={cid}",
                flush=True,
            )
            _mark_capture_ineligible(env, "box_pollution")
            return None

        sidecar = dump_episode_sidecar(
            env,
            captured_room_id=live_room,
            captured_at_iso=utc_now_iso(),
        )
        try:
            sidecar["capture_step"] = int(getattr(env, "_step_count", 0) or 0)
        except (TypeError, ValueError):
            sidecar["capture_step"] = 0
        sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

        checkpoint_id = str(completed_cp.get("checkpoint_id", "") or cid)
        route_id = str(stage.get("route_id") or "yawn_quest_v2")
        from re1_rl.go_explore_archive import attach_leg_frames
        from re1_rl.leg_replay import maybe_write_capture_tape, should_write_leg_replay

        to_state_sha = hashlib.sha256(state_path.read_bytes()).hexdigest()
        settled = not bool(unsettled_state.get("in_control", True))
        quality = compute_quality(
            live_state,
            ever_held=getattr(getattr(env, "_items", None), "ever_held", None),
            env=env,
        )
        leg_frames = None
        if should_write_leg_replay(env, completed):
            buf = getattr(env, "_leg_replay", None)
            if buf is not None:
                leg_frames = int(buf.leg_frames)
        quality = attach_leg_frames(quality, leg_frames)
        maybe_write_capture_tape(
            env,
            staging,
            completed_index=completed,
            completed_id=checkpoint_id,
            settled=settled,
            live_state=live_state,
            quality=quality,
            to_state_sha256=to_state_sha,
        )
        _log_capture_quality_vs_incumbent(
            root,
            checkpoint_index=completed,
            checkpoint_id=checkpoint_id,
            quality=quality,
            note="propose",
        )
        proposal = build_capture_proposal(
            route_id=route_id,
            checkpoint_index=completed,
            checkpoint_id=checkpoint_id,
            room_id=live_room,
            quality=quality,
            state_path=state_path,
            sidecar_path=sidecar_path,
            worker_id=os.environ.get("MACHINE_NAME"),
            capacity=capacity,
        )
        meta = {
            "route_id": route_id,
            "checkpoint_index": completed,
            "checkpoint_id": checkpoint_id,
            "room_id": live_room,
            "quality": list(quality),
            "bundle_sha256": str(proposal.get("bundle_sha256") or ""),
            "bytes": int(proposal.get("bytes") or 0),
            **{
                k: capacity[k]
                for k in (
                    "inventory_free_slots",
                    "next_checkpoint_id",
                    "next_slots_needed",
                    "inventory_feasible",
                    "captured_in_box_room",
                )
                if k in capacity
            },
        }
        (staging / CELL_META_NAME).write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        if yawn_rails_sync_enabled():
            # Learner admits; workers install via verified poll only.
            return proposal

        row = {
            "checkpoint_index": completed,
            "checkpoint_id": checkpoint_id,
            "room_id": live_room,
            "route_id": route_id,
            "quality": list(quality),
            "bundle_sha256": proposal.get("bundle_sha256", ""),
            **capacity,
        }
        installed = try_install_yawn_cell(
            root,
            checkpoint_index=completed,
            staged_dir=staging,
            quality=quality,
            row=row,
            holder="yawn_capture_local",
        )
        if installed:
            print(
                f"[yawn_capture] installed cp{completed:02d} {checkpoint_id} "
                f"room={live_room} quality={list(quality)}",
                flush=True,
            )
        return proposal if installed else None
    finally:
        env._macro_active = prev_macro
        shutil.rmtree(staging, ignore_errors=True)
