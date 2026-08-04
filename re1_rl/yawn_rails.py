"""Curated one-leg reset cells for the Yawn rails curriculum."""

from __future__ import annotations

import json
import os
import random
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
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cells"), list):
        raise ValueError(f"invalid Yawn rails cells manifest: {path}")
    if data.get("route_id") != stage.get("route_id"):
        raise ValueError(
            f"Yawn rails manifest route_id mismatch: {path}"
        )
    return data


def sample_one_leg_options(
    project_root: Path,
    stage: dict[str, Any],
    *,
    rng: random.Random,
) -> dict[str, Any]:
    """Choose a deterministic curated start and bounded checkpoint span."""
    candidates: list[dict[str, Any]] = [
        {"checkpoint_index": -1, "source": "route_initial"}
    ]
    for row in load_manifest(project_root, stage)["cells"]:
        state = project_root / str(row.get("state_path", ""))
        sidecar = project_root / str(row.get("sidecar_path", ""))
        if (
            state.is_file()
            and sidecar.is_file()
            and _sampling_row_eligible(project_root, stage, row)
        ):
            candidates.append(dict(row))
    chosen = candidates[rng.randrange(len(candidates))]
    start_index = int(chosen["checkpoint_index"]) + 1
    route_steps = list(stage.get("route_steps", []))
    remaining = max(1, len(route_steps) - start_index)
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

    Always writes the local ``states/yawn_rails/cells/cpNN`` bundle + sampling
    manifest row. The returned proposal is attached to episode infos so the
    learner can admit/replace the canonical fleet copy.
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
    next_checkpoint = planner.step_by_seq(completed + 2)
    capacity = successor_capacity(state, next_checkpoint)
    if not capacity["inventory_feasible"]:
        return None

    root = Path(env.project_root)
    cell_dir = root / "states" / "yawn_rails" / "cells" / f"cp{completed:02d}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    state_path = cell_dir / "cell.State"
    sidecar_path = cell_dir / "cell.sidecar.json"
    env.bridge.save_savestate(str(state_path))
    sidecar = dump_episode_sidecar(
        env,
        captured_room_id=str(state.get("room_id", "")),
        captured_at_iso=utc_now_iso(),
    )
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    checkpoint_id = str(
        (planner.step_by_seq(completed + 1) or {}).get("checkpoint_id", "")
    )
    room_id = str(state.get("room_id", ""))
    route_id = str(stage.get("route_id") or "yawn_quest_v2")

    from re1_rl.go_explore_capture import compute_quality
    from re1_rl.yawn_rails_sync import build_capture_proposal

    quality = compute_quality(
        state,
        ever_held=getattr(getattr(env, "_items", None), "ever_held", None),
        env=env,
    )
    proposal = build_capture_proposal(
        route_id=route_id,
        checkpoint_index=completed,
        checkpoint_id=checkpoint_id,
        room_id=room_id,
        quality=quality,
        state_path=state_path,
        sidecar_path=sidecar_path,
        worker_id=os.environ.get("MACHINE_NAME"),
        capacity=capacity,
    )

    # Local sampling manifest (learner poll will overwrite with canonical rows).
    manifest_path = root / stage["cells_manifest"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(root, stage)
    rel_state = state_path.relative_to(root).as_posix()
    rel_sidecar = sidecar_path.relative_to(root).as_posix()
    row = {
        "checkpoint_index": completed,
        "checkpoint_id": checkpoint_id,
        "room_id": room_id,
        "state_path": rel_state,
        "sidecar_path": rel_sidecar,
        "quality": list(quality),
        "bundle_sha256": proposal.get("bundle_sha256", ""),
        **capacity,
    }
    cells = [
        old for old in manifest["cells"]
        if int(old.get("checkpoint_index", -999)) != completed
    ]
    cells.append(row)
    manifest["route_id"] = route_id
    manifest["cells"] = sorted(cells, key=lambda x: int(x["checkpoint_index"]))
    tmp = manifest_path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)
    return proposal
