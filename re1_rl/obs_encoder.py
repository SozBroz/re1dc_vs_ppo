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

from re1_rl.enemy_motion import clip_vel
from re1_rl.item_box import BOX_ROOMS
from re1_rl.item_todo import ItemTracker, RoomItems, canonical_item, canonicalize
from re1_rl.memory_map import ITEM_IDS
from re1_rl.planner import OBJECTIVE_TYPES, WaypointPlanner
from re1_rl.room_graph import RoomGraph
from re1_rl.weapon_damage import AMMO_QTY_NORM, ammo_qty_norm

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
    ("poisoned", "1 = poisoned (DISABLED: always 0 until PLAYER_POISON verified)"),
    ("player_world_vx", "Jill allocentric x delta / VEL_NORM, clip [-1,1]"),
    ("player_world_vz", "Jill allocentric z delta / VEL_NORM, clip [-1,1]"),
]

GOAL_FIELDS: list[tuple[str, str]] = [
    ("goal_room_index", "target room table index / 128"),
    ("waypoint_index", "current route step / total"),
    ("waypoints_remaining", "route steps left / total"),
    ("route_hop_distance", "BFS door-hops to goal room / 20"),
    ("in_target_room", "1 = already in the goal room"),
    ("door_delta_x", "(target_x - player_x) / 4096; door, statue, or in-room pickup/use"),
    ("door_delta_z", "(target_z - player_z) / 4096; door, statue, or in-room pickup/use"),
    ("door_distance", "euclidean distance to door / statue / in-room objective / 4096"),
    ("door_bearing_sin", "sin(angle to wayfinder target - facing); + = left"),
    ("door_bearing_cos", "cos(angle to wayfinder target - facing); 1 = ahead"),
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
    ("dining_statue_knocked", "1 = dining 2F statue pushed off balcony"),
]
GOAL_BASE_DIM = len(GOAL_FIELDS)

GOAL_LOOKAHEAD_SLOTS = 6
GOAL_LOOKAHEAD_SLOT_FIELDS: list[tuple[str, str]] = [
    ("mask", "1 = checkpoint exists"),
    ("room_index", "checkpoint room table index / 128"),
    ("offset", "checkpoint offset / 5 (0 = active)"),
    *(("obj_" + name, f"objective one-hot: {name}") for name in OBJECTIVE_TYPES),
    ("required_item0_id", "first required item id / 0x4B"),
    ("required_item1_id", "second required item id / 0x4B"),
    ("required_count", "required item count / 4"),
    ("has_required_items", "1 = all checkpoint prerequisites currently held"),
    ("gained_item0_id", "first gained item id / 0x4B"),
    ("gained_item1_id", "second gained item id / 0x4B"),
    ("gained_count", "declared gained item count / 4"),
    ("target_has_box", "1 = checkpoint room has an item box"),
    ("projected_headroom", "free inventory slots after declared gains / 8"),
]
GOAL_LOOKAHEAD_SLOT_DIM = len(GOAL_LOOKAHEAD_SLOT_FIELDS)
GOAL_FIELDS.extend(
    (
        f"lookahead{slot}_{name}",
        f"checkpoint lookahead slot {slot}: {description}",
    )
    for slot in range(GOAL_LOOKAHEAD_SLOTS)
    for name, description in GOAL_LOOKAHEAD_SLOT_FIELDS
)

PROPRIO_DIM = len(PROPRIO_FIELDS)  # 28
GOAL_DIM = len(GOAL_FIELDS)

ANIM_HISTORY_LEN = 4
ANIM_RECOVERY_NORM = 32.0

BOX_FIELDS: list[tuple[str, str]] = [
    field
    for n in range(16)
    for field in (
        (f"box{n}_item_id", "item id / 0x46 (0 = empty)"),
        (f"box{n}_qty", f"quantity / {int(AMMO_QTY_NORM)}, clip [0,1]"),
    )
] + [
    ("box_free_slots", "empty box slots / 16"),
    ("in_box_room", "1 = current room has an item box"),
]

BOX_DIM = len(BOX_FIELDS)  # 34

LOGISTICS_FIELDS: list[tuple[str, str]] = [
    ("valid", "1 = route horizon is available"),
    ("required_item0_id", "required item identity / 0x4B"),
    ("required_item1_id", "required item identity / 0x4B"),
    ("required_item2_id", "required item identity / 0x4B"),
    ("required_item3_id", "required item identity / 0x4B"),
    ("required_count", "distinct required route items / 8"),
    ("gained_slots", "declared item gains before horizon end / 8"),
    ("consumed_slots", "declared consumptions before horizon end / 8"),
    ("net_slot_pressure", "(gains - consumptions) / 8"),
    ("checkpoints_remaining", "checkpoints through next box or boss / 16"),
    ("boss_ahead", "1 = horizon terminates at a fight objective"),
    ("no_box_ahead", "1 = boss/end occurs before another box"),
    ("next_box_ahead", "1 = horizon terminates at a later item box"),
    ("horizon_graph_distance", "door hops to horizon endpoint / 20"),
    ("mandatory_pickup_slots", "declared gains not also consumed / 8"),
    ("held_required_fraction", "fraction of required route items currently held"),
    ("current_free_slots", "free on-person inventory slots / 8"),
]
LOGISTICS_DIM = len(LOGISTICS_FIELDS)

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
        from re1_rl.memory_map import player_poisoned_from_state

        v[27] = 1.0 if player_poisoned_from_state(state) else 0.0
        v[28] = clip_vel(float(state.get("player_world_vx", 0)))
        v[29] = clip_vel(float(state.get("player_world_vz", 0)))
        return v

    def encode_rooms_visited(self, visited_rooms: set[str]) -> np.ndarray:
        """One-hot over the stable room table: 0 until first visit, then 1."""
        v = np.zeros(ROOM_VISITED_DIM, dtype=np.float32)
        for room_id in visited_rooms:
            idx = self.room_index.get(str(room_id))
            if idx is not None and 0 <= idx < ROOM_VISITED_DIM:
                v[idx] = 1.0
        return v

    def _compass_to_xz(
        self, state: dict[str, Any], tx: float, tz: float
    ) -> np.ndarray:
        """Egocentric 5-slot compass used by the door / statue / pickup wayfinder."""
        dx = float(tx) - float(state.get("x", 0))
        dz = float(tz) - float(state.get("z", 0))
        distance = math.hypot(dx, dz)
        facing = 2.0 * math.pi * float(state.get("facing", 0)) / FACING_FULL_CIRCLE
        relative = math.atan2(dz, dx) - facing
        return np.asarray(
            [
                float(np.clip(dx / DIST_NORM, -2.0, 2.0)),
                float(np.clip(dz / DIST_NORM, -2.0, 2.0)),
                min(distance / DIST_NORM, 2.0),
                math.sin(relative),
                math.cos(relative),
            ],
            dtype=np.float32,
        )

    def _in_room_objective_xz(
        self, state: dict[str, Any], planner: WaypointPlanner
    ) -> tuple[float, float] | None:
        """Pickup / USE world XZ when Jill is already in the checkpoint room."""
        room = str(state.get("room_id", ""))
        goal = planner.next_waypoint_room()
        if not room or goal is None or room != str(goal):
            return None
        step = planner.current_objective() or {}
        atype = planner.objective_type()
        if atype == "pickup":
            from re1_rl.spatial_encoder import ItemPositions

            positions = getattr(self, "_item_positions", None)
            if positions is None:
                positions = ItemPositions(
                    Path(__file__).resolve().parents[1] / "data" / "item_positions.json"
                )
                self._item_positions = positions
            for name in step.get("items_gained") or []:
                pos = positions.get(room, str(name))
                if pos is not None:
                    return pos
            return None
        if atype == "use_item":
            from re1_rl.story_item_use import load_story_use_sites

            required = {
                canonical_item(str(x)) for x in (step.get("required_items") or [])
            }
            for site in load_story_use_sites():
                if str(site.get("room") or "") != room:
                    continue
                if canonical_item(str(site.get("item") or "")) not in required:
                    continue
                return (float(site["x"]), float(site["z"]))
        return None

    def encode_goal(
        self,
        state: dict[str, Any],
        planner: WaypointPlanner,
        item_tracker: ItemTracker | None = None,
        room_items: RoomItems | None = None,
    ) -> np.ndarray:
        """Encode the live checkpoint objective and egocentric exit compass."""
        from re1_rl.dining_statue_puzzle import (
            encode_dining_statue_compass,
            encode_dining_statue_goal,
        )
        from re1_rl.gallery_puzzle import encode_gallery_hint

        v = np.zeros(GOAL_DIM, dtype=np.float32)
        room = str(state.get("room_id", ""))
        goal = planner.next_waypoint_room()
        total = max(planner.total_waypoints, 1)
        idx = min(planner.waypoint_index, total)

        v[0] = self._room_idx_norm(goal)
        v[1] = idx / float(total)
        v[2] = max(0, total - idx) / float(total)
        hops = self.graph.hop_distance(room, goal)
        v[3] = 1.0 if hops is None else min(float(hops) / 20.0, 1.0)
        v[4] = 1.0 if goal is not None and room == str(goal) else 0.0

        statue_compass = encode_dining_statue_compass(state, planner)
        if statue_compass is not None:
            # On statue_202, reuse the door compass for the drop-line / final shove.
            v[5:10] = statue_compass
            v[21] = 1.0
        else:
            door = self.graph.exit_toward(room, goal)
            if door is not None:
                v[5:10] = self._compass_to_xz(state, float(door.x), float(door.z))
                v[21] = 1.0
            else:
                # Already in the checkpoint room: point at the pickup / USE site
                # (fresh→emblem, in-room music notes, fireplace, …).
                target = self._in_room_objective_xz(state, planner)
                if target is not None:
                    v[5:10] = self._compass_to_xz(state, target[0], target[1])
                    v[21] = 1.0

        v[10:15] = planner.objective_one_hot()
        v[15] = min(max(self.curriculum_stage_index / 10.0, 0.0), 1.0)
        if item_tracker is not None:
            done, item_total = item_tracker.progress()
            v[16] = done / float(max(item_total, 1))
        if item_tracker is not None and room_items is not None:
            ever = item_tracker.ever_held
            v[17] = min(room_items.remaining_in_room(room, ever) / 8.0, 1.0)
            v[18] = min(room_items.key_items_remaining_in_room(room, ever) / 4.0, 1.0)
            v[22] = min(room_items.gated_in_room(room, ever) / 4.0, 1.0)

        required = {canonical_item(x) for x in planner.required_items()}
        inventory = {canonical_item(x) for x in state.get("inventory", [])}
        v[19] = 1.0 if required.issubset(inventory) else 0.0
        v[20] = 1.0 if goal is not None and room != str(goal) and hops is None else 0.0
        v[23:27] = encode_gallery_hint(state)
        v[27] = encode_dining_statue_goal(state)
        self._encode_goal_lookahead(v, state, planner)
        return v

    def _encode_goal_lookahead(
        self,
        vector: np.ndarray,
        state: dict[str, Any],
        planner: WaypointPlanner,
    ) -> None:
        """Append masked checkpoint semantics; the exit compass stays immediate."""
        inventory = {canonical_item(x) for x in state.get("inventory", [])}
        projected_free = max(0, INVENTORY_SLOTS - len(state.get("inventory", [])))
        base = GOAL_BASE_DIM
        for offset in range(GOAL_LOOKAHEAD_SLOTS):
            step = planner.peek_objective(offset)
            room = planner.peek_waypoint_room(offset)
            if step is None or room is None:
                continue
            start = base + offset * GOAL_LOOKAHEAD_SLOT_DIM
            slot = vector[start : start + GOAL_LOOKAHEAD_SLOT_DIM]
            required = [canonical_item(x) for x in planner.peek_required_items(offset)]
            gained = [canonical_item(x) for x in planner.peek_items_gained(offset)]
            projected_free = max(0, projected_free - len(gained))
            slot[0] = 1.0
            slot[1] = self._room_idx_norm(room)
            slot[2] = offset / float(max(GOAL_LOOKAHEAD_SLOTS - 1, 1))
            slot[3 + OBJECTIVE_TYPES.index(planner.peek_objective_type(offset))] = 1.0
            for item_offset, item_name in enumerate(required[:2]):
                slot[8 + item_offset] = _NAME_TO_ITEM_ID.get(item_name, 0) / float(MAX_ITEM_ID)
            slot[10] = min(len(required) / 4.0, 1.0)
            slot[11] = 1.0 if set(required).issubset(inventory) else 0.0
            for item_offset, item_name in enumerate(gained[:2]):
                slot[12 + item_offset] = _NAME_TO_ITEM_ID.get(item_name, 0) / float(MAX_ITEM_ID)
            slot[14] = min(len(gained) / 4.0, 1.0)
            slot[15] = 1.0 if str(room).upper() in BOX_ROOMS else 0.0
            slot[16] = projected_free / float(INVENTORY_SLOTS)

    def encode_logistics(
        self,
        state: dict[str, Any],
        planner: WaypointPlanner,
    ) -> np.ndarray:
        """Factual route semantics through the next later box or fight."""
        v = np.zeros(LOGISTICS_DIM, dtype=np.float32)
        rows: list[dict[str, Any]] = []
        boss_ahead = False
        next_box_ahead = False
        for offset in range(planner.waypoints_remaining):
            step = planner.peek_objective(offset)
            room = planner.peek_waypoint_room(offset)
            if step is None or room is None:
                break
            rows.append(step)
            is_current_box = offset == 0 and str(state.get("room_id", "")).upper() in BOX_ROOMS
            if step.get("action_type") == "fight":
                boss_ahead = True
                break
            if str(room).upper() in BOX_ROOMS and not is_current_box:
                next_box_ahead = True
                break
        if not rows:
            return v
        required = sorted({
            canonical_item(str(item))
            for row in rows
            for item in row.get("required_items", [])
            if canonical_item(str(item))
        })
        gained = [
            canonical_item(str(item.get("item") if isinstance(item, dict) else item))
            for row in rows
            for item in row.get("items_gained", [])
        ]
        consumed = [
            canonical_item(str(item.get("item") if isinstance(item, dict) else item))
            for row in rows
            for item in row.get("consume_before_gain", [])
        ]
        held = {
            canonical_item(str(entry[0] if isinstance(entry, (tuple, list)) else entry))
            for entry in (state.get("inventory_slots") or state.get("inventory") or [])
            if entry and (not isinstance(entry, (tuple, list)) or entry[0])
        }
        occupied = sum(
            bool(entry and (not isinstance(entry, (tuple, list)) or entry[0]))
            for entry in (state.get("inventory_slots") or state.get("inventory") or [])
        )
        v[0] = 1.0
        for i, name in enumerate(required[:4]):
            v[1 + i] = _NAME_TO_ITEM_ID.get(name, 0) / float(MAX_ITEM_ID)
        v[5] = min(len(required) / 8.0, 1.0)
        v[6] = min(len(gained) / 8.0, 1.0)
        v[7] = min(len(consumed) / 8.0, 1.0)
        v[8] = float(np.clip((len(gained) - len(consumed)) / 8.0, -1.0, 1.0))
        v[9] = min(len(rows) / 16.0, 1.0)
        v[10] = 1.0 if boss_ahead else 0.0
        v[11] = 0.0 if next_box_ahead else 1.0
        v[12] = 1.0 if next_box_ahead else 0.0
        endpoint_room = str(rows[-1].get("room_id", ""))
        hops = self.graph.hop_distance(str(state.get("room_id", "")), endpoint_room)
        v[13] = 1.0 if hops is None else min(float(hops) / 20.0, 1.0)
        v[14] = min(len([name for name in gained if name not in consumed]) / 8.0, 1.0)
        v[15] = (
            len(set(required) & held) / float(len(required))
            if required else 1.0
        )
        v[16] = max(0, INVENTORY_SLOTS - occupied) / float(INVENTORY_SLOTS)
        return v


def encode_inventory_slots(
    inventory_slots: list[tuple[str, int]] | None,
) -> np.ndarray:
    """Encode on-person inventory (8 slots): item_id / MAX, qty / AMMO_QTY_NORM."""
    v = np.zeros(INVENTORY_OBS_DIM, dtype=np.float32)
    slots = list(inventory_slots or [])[:INVENTORY_SLOTS]
    while len(slots) < INVENTORY_SLOTS:
        slots.append(("", 0))
    for i, (name, qty) in enumerate(slots):
        item_id = _NAME_TO_ITEM_ID.get(canonical_item(str(name)), 0)
        v[2 * i] = float(item_id) / float(MAX_ITEM_ID)
        v[2 * i + 1] = ammo_qty_norm(qty)
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
        v[2 * i + 1] = ammo_qty_norm(qty)
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
    if "logistics" in obs:
        out["logistics"] = explain_vector(obs["logistics"], LOGISTICS_FIELDS)
    if "weapon_card" in obs:
        from re1_rl.weapon_damage import WEAPON_CARD_FIELDS

        out["weapon_card"] = explain_vector(obs["weapon_card"], WEAPON_CARD_FIELDS)
    if "last_attack" in obs:
        from re1_rl.weapon_damage import LAST_ATTACK_FIELDS

        out["last_attack"] = explain_vector(obs["last_attack"], LAST_ATTACK_FIELDS)
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
    for section in ("proprio", "goal", "spatial", "box", "logistics", "weapon_card", "last_attack"):
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
