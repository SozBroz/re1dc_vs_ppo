"""Curated one-leg reset cells for the Yawn rails curriculum."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any

from re1_rl.pb_sidecar import dump_episode_sidecar, utc_now_iso


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
    """Choose a deterministic available curated start; never sample PB/archive."""
    candidates: list[dict[str, Any]] = [
        {"checkpoint_index": -1, "source": "route_initial"}
    ]
    for row in load_manifest(project_root, stage)["cells"]:
        state = project_root / str(row.get("state_path", ""))
        sidecar = project_root / str(row.get("sidecar_path", ""))
        if state.is_file() and sidecar.is_file():
            candidates.append(dict(row))
    chosen = candidates[rng.randrange(len(candidates))]
    start_index = int(chosen["checkpoint_index"]) + 1
    opts: dict[str, Any] = {
        "route_start_index": start_index,
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
                if f'"item": "{item}"' not in condition_text:
                    errors.append(
                        f"{cid or i}: gained item {item!r} is not required by success"
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
    route_id = str(stage.get("route_id") or "yawn_quest_v1")

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
