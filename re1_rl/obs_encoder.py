"""Structured observation encoding: proprio + goal vectors.

Every slot in each vector has a NAME. The same spec drives:
  - encoding (env.py)
  - decoding / pretty-printing (explain_obs, scripts/watch_env.py)
  - the on-screen overlay (re1_rl/overlay.py)
so the network input is never an anonymous float blob.

Layout follows docs/progress_scaffolding_design.md section 8.3/8.4
("simpler v1 proprio" variant: room index as one scalar, embedding lives in
the policy trunk later).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.item_todo import ItemTracker, RoomItems, canonical_item, canonicalize
from re1_rl.memory_map import ITEM_IDS
from re1_rl.planner import OBJECTIVE_TYPES, WaypointPlanner
from re1_rl.room_graph import RoomGraph

INVENTORY_SLOTS = 8
INVENTORY_OBS_DIM = INVENTORY_SLOTS * 2  # item_id + qty per slot
MAX_ITEM_ID = 0x4B  # highest mixed-herb id; keep in sync with spatial_encoder

_NAME_TO_ITEM_ID = {name: iid for iid, name in ITEM_IDS.items()}


def _use_item_hint(obj: dict[str, Any]) -> str | None:
    """Which held item a use_item route step wants used (first known id)."""
    for name in obj.get("required_items", []):
        cname = canonical_item(name)
        if cname in _NAME_TO_ITEM_ID:
            return cname
    return None

# --- field specs: (name, description) per slot, in vector order ---

PROPRIO_FIELDS: list[tuple[str, str]] = [
    ("hp", "player HP / 140 (fine=96+, danger<25)"),
    ("hp_delta", "HP change this step / 20, clipped"),
    ("x_local", "player x mod 4096 / 4096 (room-local)"),
    ("z_local", "player z mod 4096 / 4096 (room-local)"),
    ("y_norm", "elevation / 1024 (floor level)"),
    ("facing_sin", "sin of facing angle"),
    ("facing_cos", "cos of facing angle"),
    ("room_index", "room table index / 128"),
    ("cam_id", "fixed camera index / 16"),
    ("in_control", "1 = player has control, 0 = cutscene/door"),
    ("enemy_count", "alive enemies in room / 10 (0 until enemy RAM hunt)"),
    ("interaction_prompt", "1 = prompt visible (0 until prompt RAM hunt)"),
    ("character_id", "0 = Chris, 1 = Jill"),
    ("inv_count", "occupied inventory slots / 8"),
    ("equipped_weapon", "equipped item id / 0x46 (0 = nothing equipped)"),
    ("anim_hist0_state", "player anim byte t-3 / 255"),
    ("anim_hist0_aux", "player action aux t-3 / 255"),
    ("anim_hist0_recovery", "recovery timer t-3 / 32"),
    ("anim_hist1_state", "player anim byte t-2 / 255"),
    ("anim_hist1_aux", "player action aux t-2 / 255"),
    ("anim_hist1_recovery", "recovery timer t-2 / 32"),
    ("anim_hist2_state", "player anim byte t-1 / 255"),
    ("anim_hist2_aux", "player action aux t-1 / 255"),
    ("anim_hist2_recovery", "recovery timer t-1 / 32"),
    ("anim_hist3_state", "player anim byte t / 255"),
    ("anim_hist3_aux", "player action aux t / 255"),
    ("anim_hist3_recovery", "recovery timer t / 32"),
    ("poisoned", "1 = poisoned (RAM candidate 0x800C51A1)"),
]

GOAL_FIELDS: list[tuple[str, str]] = [
    ("goal_room_index", "target room table index / 128"),
    ("waypoint_index", "current route step / total"),
    ("waypoints_remaining", "route steps left / total"),
    ("route_hop_distance", "BFS door-hops to goal room / 20"),
    ("in_target_room", "1 = already in the goal room"),
    ("door_delta_x", "(door_x - player_x) / 4096, clip [-2,2]"),
    ("door_delta_z", "(door_z - player_z) / 4096, clip [-2,2]"),
    ("door_distance", "euclidean distance to exit door / 4096"),
    ("door_bearing_sin", "sin(angle to door - facing); + = door to the left"),
    ("door_bearing_cos", "cos(angle to door - facing); 1 = dead ahead"),
    ("obj_navigate", "objective one-hot: navigate"),
    ("obj_pickup", "objective one-hot: pick up item"),
    ("obj_use_item", "objective one-hot: use item"),
    ("obj_fight", "objective one-hot: fight"),
    ("obj_scripted", "objective one-hot: scripted macro owns this step"),
    ("curriculum_stage", "curriculum stage index / 10"),
    ("item_todo_progress", "route items acquired / total (ever-held)"),
    ("items_left_here", "pickups never held in current room / 8"),
    ("key_items_left_here", "KEY pickups never held in current room / 4"),
    ("has_required_items", "1 = holding all prereq items for current waypoint"),
    ("wrong_room_flag", "1 = current room not on known route subgraph"),
    ("doors_available", "1 = door table knows the exit toward goal"),
    ("gated_items_here", "pickups here locked behind progression / 4 (ignore for now, come back)"),
    ("gallery_bearing_sin", "sin(angle to next Gallery portrait - facing)"),
    ("gallery_bearing_cos", "cos(angle to next Gallery portrait - facing)"),
    ("gallery_distance", "distance to next Gallery portrait / 4096"),
    ("gallery_progress", "correct Gallery switches / 6"),
]

PROPRIO_DIM = len(PROPRIO_FIELDS)  # 28
GOAL_DIM = len(GOAL_FIELDS)  # 24

ANIM_HISTORY_LEN = 4
ANIM_RECOVERY_NORM = 32.0

BOX_FIELDS: list[tuple[str, str]] = [
    field
    for n in range(16)
    for field in (
        (f"box{n}_item_id", "item id / 0x46 (0 = empty)"),
        (f"box{n}_qty", "quantity / 15, clip [0,1]"),
    )
] + [
    ("box_free_slots", "empty box slots / 16"),
    ("in_box_room", "1 = current room has an item box"),
]

BOX_DIM = len(BOX_FIELDS)  # 34

# Episode-local visited-room flags; indices match ``room_index`` (116 rooms, /128 pad).
ROOM_VISITED_DIM = 128

FACING_FULL_CIRCLE = 4096.0
DIST_NORM = 4096.0


class ObsEncoder:
    """Stateless-ish encoder: state dict + planner + graph -> named vectors."""

    def __init__(
        self,
        rooms_path: str | Path,
        graph: RoomGraph,
        curriculum_stage_index: int = 0,
    ) -> None:
        with Path(rooms_path).open(encoding="utf-8") as f:
            rooms = json.load(f)
        # stable alphanumeric order -> index; 116 rooms, normalized /128
        self.room_index: dict[str, int] = {
            rid: i for i, rid in enumerate(sorted(rooms.keys()))
        }
        self.graph = graph
        self.curriculum_stage_index = curriculum_stage_index

    def _room_idx_norm(self, room_id: str | None) -> float:
        if room_id is None:
            return 0.0
        return self.room_index.get(str(room_id), 127) / 128.0

    def encode_proprio(self, state: dict[str, Any], prev_hp: int) -> np.ndarray:
        v = np.zeros(PROPRIO_DIM, dtype=np.float32)
        hp = float(state.get("hp", 0))
        theta = 2.0 * math.pi * float(state.get("facing", 0)) / FACING_FULL_CIRCLE
        v[0] = hp / 140.0
        v[1] = float(np.clip((hp - prev_hp) / 20.0, -1.0, 1.0))
        v[2] = (float(state.get("x", 0)) % DIST_NORM) / DIST_NORM
        v[3] = (float(state.get("z", 0)) % DIST_NORM) / DIST_NORM
        v[4] = float(state.get("y", 0)) / 1024.0
        v[5] = math.sin(theta)
        v[6] = math.cos(theta)
        v[7] = self._room_idx_norm(state.get("room_id"))
        v[8] = float(state.get("cam_id", 0)) / 16.0
        v[9] = 1.0 if state.get("in_control", True) else 0.0
        alive = [e for e in state.get("enemies", []) or [] if e.get("alive", True)]
        v[10] = min(len(alive), 10) / 10.0
        v[11] = 1.0 if state.get("interaction_prompt") else 0.0
        v[12] = float(state.get("character_id", 1))
        v[13] = len(state.get("inventory", [])) / 8.0
        v[14] = float(state.get("equipped_weapon_id", 0)) / float(MAX_ITEM_ID)
        hist = state.get("anim_history") or []
        for i in range(ANIM_HISTORY_LEN):
            base = 15 + i * 3
            if i < len(hist):
                anim, aux, rec = hist[i]
                v[base] = float(anim) / 255.0
                v[base + 1] = float(aux) / 255.0
                v[base + 2] = float(rec) / ANIM_RECOVERY_NORM
        v[27] = 1.0 if state.get("poisoned") else 0.0
        return v

    def encode_rooms_visited(self, visited_rooms: set[str]) -> np.ndarray:
        """One-hot over the stable room table: 0 until first visit, then 1."""
        v = np.zeros(ROOM_VISITED_DIM, dtype=np.float32)
        for room_id in visited_rooms:
            idx = self.room_index.get(str(room_id))
            if idx is not None and 0 <= idx < ROOM_VISITED_DIM:
                v[idx] = 1.0
        return v

    def encode_goal(
        self,
        state: dict[str, Any],
        planner: WaypointPlanner,
        item_tracker: ItemTracker | None = None,
        room_items: RoomItems | None = None,
    ) -> np.ndarray:
        """Checkpoint compass / planner goal vector.

        Disabled for exploration training: always zero so the policy cannot
        read the scripted route. Rewards use per-episode new-room/cutscene
        bonuses instead of checkpoint-path shaping.
        """
        from re1_rl.gallery_puzzle import encode_gallery_hint

        del planner, item_tracker, room_items
        v = np.zeros(GOAL_DIM, dtype=np.float32)
        v[-4:] = encode_gallery_hint(state)
        return v


def encode_inventory_slots(
    inventory_slots: list[tuple[str, int]] | None,
) -> np.ndarray:
    """Encode on-person inventory (8 slots): item_id / MAX, qty / 15."""
    v = np.zeros(INVENTORY_OBS_DIM, dtype=np.float32)
    slots = list(inventory_slots or [])[:INVENTORY_SLOTS]
    while len(slots) < INVENTORY_SLOTS:
        slots.append(("", 0))
    for i, (name, qty) in enumerate(slots):
        item_id = _NAME_TO_ITEM_ID.get(canonical_item(str(name)), 0)
        v[2 * i] = float(item_id) / float(MAX_ITEM_ID)
        v[2 * i + 1] = float(np.clip(int(qty) / 15.0, 0.0, 1.0))
    return v


def encode_box(box: list[tuple[int, int]] | None, *, in_box_room: bool) -> np.ndarray:
    """Encode up to 16 item-box slots plus room-presence flag."""
    v = np.zeros(BOX_DIM, dtype=np.float32)
    slots: list[tuple[int, int]] = [(0, 0)] * 16
    if box:
        for i, pair in enumerate(box[:16]):
            slots[i] = pair
    free = 0
    for i, (item_id, qty) in enumerate(slots):
        v[2 * i] = item_id / float(MAX_ITEM_ID)
        v[2 * i + 1] = float(np.clip(qty / 15.0, 0.0, 1.0))
        if item_id == 0:
            free += 1
    v[32] = free / 16.0
    v[33] = 1.0 if in_box_room else 0.0
    return v


# --- human-readable decoding ---

def explain_vector(vec: np.ndarray, fields: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {"i": i, "name": name, "value": round(float(vec[i]), 4), "meaning": desc}
        for i, (name, desc) in enumerate(fields)
    ]


def explain_obs(obs: dict[str, np.ndarray]) -> dict[str, Any]:
    """Decode a full env observation into named, annotated fields."""
    from re1_rl.spatial_encoder import SPATIAL_FIELDS

    out: dict[str, Any] = {}
    if "proprio" in obs:
        out["proprio"] = explain_vector(obs["proprio"], PROPRIO_FIELDS)
    if "goal" in obs:
        out["goal"] = explain_vector(obs["goal"], GOAL_FIELDS)
    if "spatial" in obs:
        out["spatial"] = explain_vector(obs["spatial"], SPATIAL_FIELDS)
    if "box" in obs:
        out["box"] = explain_vector(obs["box"], BOX_FIELDS)
    if "visited" in obs:
        vm = obs["visited"]
        out["visited"] = {"shape": list(vm.shape),
                          "cells_seen": int(vm.sum())}
    if "rooms_visited" in obs:
        rv = obs["rooms_visited"]
        on = [i for i, x in enumerate(rv) if float(x) > 0.5]
        out["rooms_visited"] = {"dim": int(rv.shape[0]), "count": len(on), "indices": on}
    if "frame" in obs:
        f = obs["frame"]
        out["frame"] = {"shape": list(f.shape), "dtype": str(f.dtype),
                        "mean": round(float(f.mean()), 2)}
    return out


def format_obs_table(obs: dict[str, np.ndarray], *, spatial_nonzero_only: bool = True) -> str:
    """Multi-line console table of every obs field. For humans.

    The 119-slot spatial vector is mostly zero padding; by default only
    non-zero rows (plus section scalars) are printed.
    """
    lines: list[str] = []
    ex = explain_obs(obs)
    for section in ("proprio", "goal", "spatial", "box"):
        if section not in ex:
            continue
        lines.append(f"--- {section} ---")
        for row in ex[section]:
            head = row["name"].split("_", 1)[0]
            is_slot_field = head and head[-1].isdigit()  # item0_/enemy2_/box3_
            if (section in ("spatial", "box") and spatial_nonzero_only
                    and row["value"] == 0.0 and is_slot_field):
                continue
            lines.append(f"  [{row['i']:3d}] {row['name']:<20} {row['value']:>8.4f}  {row['meaning']}")
    if "visited" in ex:
        vm = ex["visited"]
        lines.append(f"--- visited --- shape={vm['shape']} cells_seen={vm['cells_seen']}")
    if "rooms_visited" in ex:
        rv = ex["rooms_visited"]
        lines.append(
            f"--- rooms_visited --- count={rv['count']} indices={rv['indices']}"
        )
    if "frame" in ex:
        fr = ex["frame"]
        lines.append(f"--- frame --- shape={fr['shape']} mean={fr['mean']}")
    return "\n".join(lines)
