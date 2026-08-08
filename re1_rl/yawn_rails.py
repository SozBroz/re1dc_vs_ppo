"""Curated one-leg reset cells for the Yawn rails curriculum."""

from __future__ import annotations

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
    for slot in range(min(N_WITHDRAW_ACTIONS, len(box))):
        action = WITHDRAW_ACTION_BASE + slot
        if action >= len(mask) or not mask[action]:
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
    next_checkpoint = _checkpoint_after_row(project_root, stage, checkpoint_index)
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
    data = json.loads(path.read_text(encoding="utf-8-sig"))
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


def reset_latest_only_from_env() -> bool:
    """``RE1_YAWN_RESET_LATEST_ONLY=1`` — always start from the newest loadable cell."""
    raw = os.environ.get(_RESET_LATEST_ONLY_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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


def sample_one_leg_options(
    project_root: Path,
    stage: dict[str, Any],
    *,
    rng: random.Random,
) -> dict[str, Any]:
    """Choose a curated start and bounded checkpoint span.

    Default: 50% latest; 50% uniform over any loadable cp18+ cell.
    ``RE1_YAWN_PAYFORWARD_RIPPLE=1`` enables fight-cliff ripple mix instead.
    ``RE1_YAWN_RESET_LATEST_ONLY=1`` forces the newest cell.
    """
    cells = eligible_reset_cells(iter_loadable_cells(project_root, stage))
    latest_only = reset_latest_only_from_env()
    from re1_rl.yawn_rails_plr import plr_enabled_from_env, sample_plr_options

    if plr_enabled_from_env() and not latest_only:
        candidates = list(cells)
        return sample_plr_options(project_root, stage, candidates, rng=rng)
    if not latest_only:
        from re1_rl.yawn_rails_payforward import sample_payforward_options

        pf = sample_payforward_options(project_root, stage, cells, rng=rng)
        if pf is not None:
            return pf
    chosen = _choose_reset_candidate(cells, rng=rng, latest_only=latest_only)
    start_index = int(chosen["checkpoint_index"]) + 1
    route_steps = list(stage.get("route_steps", []))
    remaining = max(1, len(route_steps) - start_index)
    # Global legs_per_episode remains a hard cap for non-PLR mode; PLR widens
    # per endpoint instead of jumping the whole curriculum to 6-leg episodes.
    leg_span = min(max(1, int(stage.get("legs_per_episode", 1))), remaining)
    opts: dict[str, Any] = {
        "route_start_index": start_index,
        "leg_span": leg_span,
        "reset_source": "route_cell" if start_index else "route_initial",
    }
    if start_index:
        opts["pb_bundle"] = {
            "state_path": str(chosen["state_path"]),
            "sidecar_path": str(chosen["sidecar_path"]),
            "source": "yawn_rails",
        }
    return opts


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
        if room == "205":
            errors.append(f"{cid or i}: room 205 is not on the approved route")
        if i:
            prev_room = str(route[i - 1].get("room_id", ""))
            if prev_room != room and graph.hop_distance(prev_room, room) is None:
                errors.append(f"{route[i - 1].get('checkpoint_id')}->{cid}: no legal room path")
    return errors


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
    stage = getattr(env, "_stage", {})
    if stage.get("mode") != "yawn_rails":
        return None
    if not state.get("in_control", True) or state.get("dead"):
        return None
    planner = env._planner
    completed = int(planner.waypoint_index) - 1
    if completed < 0 or completed >= planner.total_waypoints - 1:
        return None
    completed_cp = planner.step_by_seq(completed + 1) or {}
    expected_room = str(completed_cp.get("room_id", "") or "")
    room_id = str(state.get("room_id", "") or "")
    cid = str(completed_cp.get("checkpoint_id", "") or "")
    # Refuse door-threshold / cutscene-spoof captures (Wesker→fake 106, Kenneth
    # at 105→104 entry pose before tea-room settle, etc.).
    from re1_rl.barry_rescue_checkpoint import barry_rescue_capture_room_ok

    if expected_room and room_id.upper() != expected_room.upper():
        if not barry_rescue_capture_room_ok(cid, room_id, expected_room):
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
        return None
    if cid == "kenneth_104" and not any(str(k).startswith("104:") for k in ledgers):
        return None
    if cid == "barry_return_105" and not any(
        str(k).startswith("105:2:s1") for k in ledgers
    ):
        return None
    if cid == "main_hall_106" and not any(str(k).startswith("106:") for k in ledgers):
        return None
    # First climb to 203 has no cinema at this story beat — do not require 203:.
    next_checkpoint = planner.step_by_seq(completed + 2)
    capacity = successor_capacity(state, next_checkpoint)
    if not capacity["inventory_feasible"]:
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
                if not barry_rescue_capture_room_ok(cid, live_room, expected_room):
                    print(
                        f"[yawn_capture] reject pre-save room drift "
                        f"live={live_room!r} expected={expected_room!r} cp={cid}",
                        flush=True,
                    )
                    return False
            if not live_state.get("in_control", True) or live_state.get("dead"):
                print(
                    f"[yawn_capture] reject pre-save not settled "
                    f"in_control={live_state.get('in_control')} "
                    f"dead={live_state.get('dead')} cp={cid}",
                    flush=True,
                )
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
                    return False
                if expected_room and after_room.upper() != expected_room.upper():
                    if not barry_rescue_capture_room_ok(
                        cid, after_room, expected_room
                    ):
                        print(
                            f"[yawn_capture] reject post-save room drift "
                            f"after={after_room!r} expected={expected_room!r} cp={cid}",
                            flush=True,
                        )
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
        if pollution:
            print(
                f"[yawn_capture] reject box pollution {pollution} cp={cid}",
                flush=True,
            )
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
        quality = compute_quality(
            live_state,
            ever_held=getattr(getattr(env, "_items", None), "ever_held", None),
            env=env,
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
