"""Planner-loyal PPO: follow LLM-authored atomic steps; divert = terminal.

Reward contract (imperator 2026-08-25):
- Keep: stepwise contempt, damage taken, damage dealt, heal-from-HP rewards.
- Heal-use tax: green/blue herb -0.05; stronger heals -0.10.
- Completing the current planner step: +8 scaled by leftover 12m cell budget
  (``+8 * leftover_frac``); then rearm a fresh 12m wall for the next step.
- Divert (wrong room / unplanned pickup / unplanned box / typewriter save): -4, episode end.
- Cell timer: flat 12 minutes only (no custom yawn_cell_timeouts.json times).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from re1_rl.item_todo import canonical_item

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHUNK = ROOT / "data" / "planner_chunks" / "cp05_shield_key.json"

PLANNER_MAX_STEPS = 20
PLANNER_STEP_SUCCESS_REWARD = 8.0
PLANNER_DIVERT_PENALTY = -4.0
PLANNER_TIMEOUT_PENALTY = -4.0
HEAL_USE_TAX_LIGHT = -0.05  # green / blue herb
HEAL_USE_TAX_STRONG = -0.10  # sprays, red mixes, multi-herb mixes

# Scalar reward keys under strict planner-loyal (telemetry aliases excluded).
PLANNER_LOYAL_SCALAR_KEYS: frozenset[str] = frozenset(
    {
        "step",
        "softlock",
        "hp",
        "death",
        "enemy_damage",
        "enemy_kill",
        "attack_miss",
        "ammo_spend",
        "ammo_waste",
        "combat_overkill",
        "shotgun_dog_hit",
        "heavy_weapon_fodder_hit",
        "attack_dry_fire",
        "attack_macro_failure",
        "heal_use_tax",
        "planner_step_success",
        "planner_divert",
        "planner_timeout",
    }
)

# Populated for capture / terminal paths; never summed into the scalar reward.
PLANNER_LOYAL_TELEMETRY_KEYS: frozenset[str] = frozenset(
    {
        "wrong_room",
        "checkpoint_success",
        "checkpoint_timeout",
    }
)

# Strategy-memory / almanac keys removed from the obs Dict under planner-loyal
# (not zeroed). World tower is omitted when ``world_state`` is absent.
PLANNER_LOYAL_OMIT_OBS_KEYS = frozenset(
    {
        "history",
        "acquisitions",
        "rooms_visited",
        "cutscene_ledger",
        "milestones",
        "maps_files",
        "affordances",
        "world_state",
    }
)

LIGHT_HEAL_USE_ITEMS = frozenset({"green_herb", "blue_herb"})
STRONG_HEAL_USE_ITEMS = frozenset(
    {
        "first_aid_spray",
        "first_aid_spray_alt",
        "red_herb",
        "mixed_herbs_gr",
        "mixed_herbs_gg",
        "mixed_herbs_gb",
        "mixed_herbs_grb",
        "mixed_herbs_ggg",
        "mixed_herbs_ggb",
    }
)

# Ops encoded for the policy (one-hot order).
PLANNER_OP_TYPES = (
    "traverse",
    "acquire",
    "objective",
    "do_puzzle",
    "trigger_cutscene",
    "boss",
    "use_box",
)


def load_chunk(path: str | Path | None = None) -> dict[str, Any]:
    chunk_path = Path(path) if path else DEFAULT_CHUNK
    with chunk_path.open(encoding="utf-8") as handle:
        return json.load(handle)


class PlannerLoyalQueue:
    """Pop-front queue of up to 20 LLM-authored steps."""

    def __init__(self, chunk: dict[str, Any] | None = None) -> None:
        data = chunk or load_chunk()
        raw_steps = list(data.get("steps") or [])
        self.chunk_id = str(data.get("chunk_id") or "unknown")
        self.end_anchor = str(data.get("end_anchor_beat_id") or "")
        self._steps: list[dict[str, Any]] = []
        for index, step in enumerate(raw_steps[:PLANNER_MAX_STEPS]):
            row = dict(step)
            row["n"] = int(row.get("n") or index + 1)
            op = str(row.get("op") or "")
            if op == "use_key_item":
                row["op"] = "objective"
            self._steps.append(row)
        self._index = 0
        self.step_success_pending = False
        self.divert_reason: str | None = None
        self._start_held: set[str] = set()
        self._start_qty_totals: dict[str, int] = {}
        self._satisfied_pickups: set[str] = set()

    @property
    def remaining(self) -> list[dict[str, Any]]:
        return list(self._steps[self._index :])

    @property
    def current(self) -> dict[str, Any] | None:
        if self._index >= len(self._steps):
            return None
        return self._steps[self._index]

    @property
    def done(self) -> bool:
        return self._index >= len(self._steps)

    @property
    def index(self) -> int:
        return self._index

    def reset(self) -> None:
        self._index = 0
        self.step_success_pending = False
        self.divert_reason = None
        self._start_held = set()
        self._start_qty_totals = {}
        self._satisfied_pickups = set()

    def note_start_inventory(self, state: dict[str, Any]) -> None:
        """Snapshot episode-start inventory (sidecar / live RAM after reset)."""
        self._start_qty_totals = _inventory_qty_totals(state)
        self._start_held = {
            name for name, qty in self._start_qty_totals.items() if qty > 0
        }

    def seek(self, index: int) -> None:
        """Jump queue to ``index`` (0 = first step; len(steps) = done)."""
        n = len(self._steps)
        self._index = max(0, min(int(index), n))
        self.step_success_pending = False
        self.divert_reason = None
        self._rebuild_satisfied_pickups()

    def _rebuild_satisfied_pickups(self) -> None:
        satisfied: set[str] = set()
        for step in self._steps[: self._index]:
            if str(step.get("op") or "") != "acquire":
                continue
            pickup_id = str(step.get("pickup_id") or "")
            if pickup_id:
                satisfied.add(pickup_id)
        self._satisfied_pickups = satisfied

    def _acquire_sibling_pile_ids(self, room: str, item: str) -> list[str]:
        siblings: list[str] = []
        for step in self._steps:
            if str(step.get("op") or "") != "acquire":
                continue
            pickup_id = str(step.get("pickup_id") or "")
            step_room, step_item, pile = _pickup_id_parts(pickup_id)
            if step_room == room and step_item == item and pile is not None:
                siblings.append(pickup_id)
        return siblings

    def _should_skip_acquire_at_start(self, pickup_id: str) -> bool:
        if pickup_id in self._satisfied_pickups:
            return True
        room, item, pile = _pickup_id_parts(pickup_id)
        if not item:
            return False
        siblings = self._acquire_sibling_pile_ids(room, item)
        if len(siblings) > 1:
            # Numbered piles (e.g. 104:handgun_bullets:1 vs :2): never skip from
            # generic "already holding ammo"; only exact pickup_ids satisfied.
            return False
        return item in self._start_held

    def _unique_acquire_now_held(self, state: dict[str, Any]) -> bool:
        """True when the current unique acquire's item is in inventory now."""
        step = self.current or {}
        if str(step.get("op") or "") != "acquire":
            return False
        pickup_id = str(step.get("pickup_id") or "")
        room, item, _pile = _pickup_id_parts(pickup_id)
        if not item or pickup_id in self._satisfied_pickups:
            return False
        if len(self._acquire_sibling_pile_ids(room, item)) > 1:
            return False
        if item in self._start_held:
            return False
        held = _inventory_qty_totals(state)
        return int(held.get(item, 0) or 0) > 0

    def _skip_satisfied_acquires(self) -> None:
        """Skip acquire steps already satisfied at episode start."""
        while True:
            step = self.current
            if not step or str(step.get("op") or "") != "acquire":
                return
            pickup_id = str(step.get("pickup_id") or "")
            if self._should_skip_acquire_at_start(pickup_id):
                self._index += 1
                self._rebuild_satisfied_pickups()
                continue
            return

    def target_room(self) -> str | None:
        step = self.current
        if not step:
            return None
        op = str(step.get("op") or "")
        if op == "traverse":
            edge = str(step.get("edge_id") or "")
            if "->" in edge:
                return edge.split("->", 1)[1]
            return None
        if op == "acquire":
            pickup_id = str(step.get("pickup_id") or "")
            room, _item, _pile = _pickup_id_parts(pickup_id)
            if room:
                return room
        return str(step.get("room_id") or "") or None

    def evaluate_transition(
        self,
        *,
        prev_state: dict[str, Any],
        state: dict[str, Any],
        box_opened: bool = False,
        typewriter_save_complete: bool = False,
    ) -> dict[str, Any]:
        """Return reward flags for this env step under planner loyalty."""
        result = {
            "step_success": False,
            "divert": False,
            "divert_reason": None,
            "heal_use_tax": 0.0,
        }
        if self.done:
            return result

        self._skip_satisfied_acquires()
        if self.done:
            return result

        step = self.current or {}
        op = str(step.get("op") or "")
        prev_room = str(prev_state.get("room_id") or "")
        room = str(state.get("room_id") or "")

        # Heal-use tax from inventory disappearance of heal items (not box).
        tax = _heal_use_tax(prev_state, state)
        result["heal_use_tax"] = tax

        if typewriter_save_complete or _ink_ribbon_consumed(prev_state, state):
            result["divert"] = True
            result["divert_reason"] = (
                "unplanned_typewriter_save"
                if typewriter_save_complete
                else "unplanned_ink_ribbon_use"
            )
            self.divert_reason = result["divert_reason"]
            return result

        # Divert: unplanned box open.
        if box_opened and op != "use_box":
            result["divert"] = True
            result["divert_reason"] = "unplanned_box"
            self.divert_reason = result["divert_reason"]
            return result

        # Unique key acquire (music_notes, gold_emblem, …): complete if the
        # item is held now and was not held at episode start. File/document
        # cinema often starts skip *after* RAM already has the item, so the
        # inventory rising edge is gone. Numbered ammo piles still need a qty
        # edge (same sibling rule as reset skip).
        if self._unique_acquire_now_held(state):
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            self._rebuild_satisfied_pickups()
            return result

        # Divert: unplanned room change.
        if room and prev_room and room != prev_room:
            if op != "traverse":
                result["divert"] = True
                result["divert_reason"] = f"unplanned_room:{prev_room}->{room}"
                self.divert_reason = result["divert_reason"]
                return result
            edge = str(step.get("edge_id") or "")
            expected = edge.split("->", 1)[1] if "->" in edge else ""
            if room != expected:
                result["divert"] = True
                result["divert_reason"] = f"wrong_traverse:{edge} got {room}"
                self.divert_reason = result["divert_reason"]
                return result
            # Correct traverse completed.
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            return result

        # Divert / complete: pickups
        gained = _inventory_gains(prev_state, state)
        if gained:
            want = str(step.get("pickup_id") or "")
            _, want_item, _ = _pickup_id_parts(want)
            matched = op == "acquire" and _pickup_matches_gain(want, gained)
            planned = {want_item} if matched else set()
            unexpected = gained - planned
            if op != "acquire":
                if unexpected:
                    result["divert"] = True
                    result["divert_reason"] = f"unplanned_pickup:{sorted(unexpected)}"
                    self.divert_reason = result["divert_reason"]
                return result
            if not matched:
                result["divert"] = True
                result["divert_reason"] = f"wrong_pickup want={want} got={sorted(gained)}"
                self.divert_reason = result["divert_reason"]
                return result
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            self._rebuild_satisfied_pickups()
            return result

        # Objective / puzzle / cutscene / boss completion via story_use or flags.
        if op in {"objective", "do_puzzle", "trigger_cutscene", "boss"}:
            site = str(step.get("site_id") or "")
            story = str(state.get("story_use_success") or "")
            if site and story and story == site:
                result["step_success"] = True
                self._index += 1
                self.step_success_pending = True
                return result
            # Gallery / puzzle progress hooks can be added later.

        if op == "use_box" and box_opened:
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
            return result

        return result


def _ink_ribbon_consumed(prev_state: dict[str, Any], state: dict[str, Any]) -> bool:
    from re1_rl.typewriter_save import count_ink_ribbons

    before = int(count_ink_ribbons(prev_state))
    after = int(count_ink_ribbons(state))
    return before > 0 and after < before


def _pickup_id_parts(pickup_id: str) -> tuple[str, str, str | None]:
    parts = str(pickup_id or "").split(":")
    if len(parts) >= 3:
        return parts[0], canonical_item(parts[1]), parts[2]
    if len(parts) == 2:
        return parts[0], canonical_item(parts[1]), None
    if parts:
        return "", canonical_item(parts[0]), None
    return "", "", None


def _pickup_matches_gain(pickup_id: str, gained: set[str]) -> bool:
    if pickup_id in gained:
        return True
    _room, item, _pile = _pickup_id_parts(pickup_id)
    if not item:
        return False
    return item in gained


def _inventory_qty_totals(state: dict[str, Any]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for entry in state.get("inventory_slots") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("item")
            qty = int(entry.get("qty", 1) or 0)
        elif isinstance(entry, (list, tuple)) and entry:
            name = entry[0]
            qty = int(entry[1]) if len(entry) > 1 else 1
        else:
            name = None
            qty = 0
        if not name:
            continue
        key = canonical_item(str(name))
        totals[key] = totals.get(key, 0) + max(qty, 0)
    return totals


def _inventory_gains(prev_state: dict[str, Any], state: dict[str, Any]) -> set[str]:
    prev = _inventory_qty_totals(prev_state)
    cur = _inventory_qty_totals(state)
    gained: set[str] = set()
    for name, qty in cur.items():
        if qty > prev.get(name, 0):
            gained.add(name)
    for name in state.get("new_items") or []:
        key = canonical_item(str(name))
        if key:
            gained.add(key)
    return gained


def _heal_use_tax(prev_state: dict[str, Any], state: dict[str, Any]) -> float:
    prev = _inventory_qty_totals(prev_state)
    cur = _inventory_qty_totals(state)
    tax = 0.0
    for name, before in prev.items():
        after = cur.get(name, 0)
        if after >= before:
            continue
        lost = before - after
        if name in LIGHT_HEAL_USE_ITEMS:
            tax += HEAL_USE_TAX_LIGHT * lost
        elif name in STRONG_HEAL_USE_ITEMS:
            tax += HEAL_USE_TAX_STRONG * lost
    return tax


def encode_planner_queue(
    queue: PlannerLoyalQueue | None,
    *,
    max_steps: int = PLANNER_MAX_STEPS,
) -> list[float]:
    """Flat encoding of the *remaining* queue (completed steps already popped).

    Slot 0 is always the current order; slots 1..19 are futures slid forward
    after each successful step (``PlannerLoyalQueue._index`` advances).
    """
    cur_dim = 1 + len(PLANNER_OP_TYPES) + 1 + 1 + 1  # 11
    fut_dim = 1 + len(PLANNER_OP_TYPES) + 1  # 9
    out = [0.0] * (cur_dim + (max_steps - 1) * fut_dim)
    if queue is None:
        return out
    remaining = queue.remaining[:max_steps]
    if not remaining:
        return out

    room_index = _room_index_map()

    def room_norm(room_id: str | None) -> float:
        if not room_id:
            return 0.0
        return float(room_index.get(str(room_id), 0)) / 128.0

    def fill_op(dest: list[float], offset: int, op: str) -> None:
        if op in PLANNER_OP_TYPES:
            dest[offset + PLANNER_OP_TYPES.index(op)] = 1.0

    def stable_unit(token: str) -> float:
        """Process-stable [0,1] id for pickup_id / site_id (not Python hash())."""
        if not token:
            return 0.0
        digest = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:2], "big") / 65535.0

    cur = remaining[0]
    out[0] = 1.0
    fill_op(out, 1, str(cur.get("op") or ""))
    if str(cur.get("op")) == "traverse":
        edge = str(cur.get("edge_id") or "")
        tgt = edge.split("->", 1)[1] if "->" in edge else None
    else:
        tgt = str(cur.get("room_id") or "") or None
    out[1 + len(PLANNER_OP_TYPES)] = room_norm(tgt)
    out[1 + len(PLANNER_OP_TYPES) + 1] = stable_unit(str(cur.get("pickup_id") or ""))
    out[1 + len(PLANNER_OP_TYPES) + 2] = stable_unit(str(cur.get("site_id") or ""))

    base = cur_dim
    for slot, step in enumerate(remaining[1:max_steps]):
        o = base + slot * fut_dim
        out[o] = 1.0
        fill_op(out, o + 1, str(step.get("op") or ""))
        if str(step.get("op")) == "traverse":
            edge = str(step.get("edge_id") or "")
            rt = edge.split("->", 1)[1] if "->" in edge else None
        else:
            rt = str(step.get("room_id") or "") or None
        out[o + 1 + len(PLANNER_OP_TYPES)] = room_norm(rt)
    return out


_ROOM_INDEX_CACHE: dict[str, int] | None = None


def _room_index_map() -> dict[str, int]:
    global _ROOM_INDEX_CACHE
    if _ROOM_INDEX_CACHE is not None:
        return _ROOM_INDEX_CACHE
    rooms = json.loads((ROOT / "data" / "rooms.json").read_text(encoding="utf-8"))
    ids = sorted(k for k in rooms if not str(k).startswith("_"))
    _ROOM_INDEX_CACHE = {rid: i for i, rid in enumerate(ids)}
    return _ROOM_INDEX_CACHE


PLANNER_QUEUE_DIM = 11 + 19 * 9  # 182

# Route-admin goal scalars the policy should ignore once the planner owns strategy.
# Indices match obs_encoder.GOAL_FIELDS (lookahead slots sit after these).
_ROUTE_ADMIN_GOAL_NAMES = (
    "waypoint_index",
    "waypoints_remaining",
    "curriculum_stage",
    "item_todo_progress",
    "wrong_room_flag",
)


def scalarize_planner_loyal_reward(bd: dict[str, float]) -> float:
    """Sum only documented planner-loyal scalar channels."""
    from re1_rl.reward import REWARD_SCALE

    total = sum(float(bd.get(key, 0.0) or 0.0) for key in PLANNER_LOYAL_SCALAR_KEYS)
    return float(total) * REWARD_SCALE


def validate_planner_loyal_stage(stage: dict[str, Any]) -> None:
    """Fail closed when planner flag and curriculum disagree."""
    mode = str(stage.get("mode") or "")
    if mode != "planner_loyal":
        raise ValueError(
            "RE1_PLANNER_LOYAL=1 requires curriculum mode=planner_loyal, "
            f"got {mode!r}"
        )
    if stage.get("route_steps"):
        raise ValueError(
            "planner_loyal curriculum must not supply legacy route_steps "
            "(queue owns navigation)"
        )


def validate_planner_loyal_chunk(chunk: dict[str, Any]) -> None:
    steps = list(chunk.get("steps") or [])
    if not steps:
        raise ValueError("planner loyal chunk has no steps")
    if len(steps) > PLANNER_MAX_STEPS:
        raise ValueError(
            f"planner loyal chunk exceeds PLANNER_MAX_STEPS={PLANNER_MAX_STEPS}"
        )


def _planner_op_objective_index(op: str) -> int | None:
    """Map planner op → goal objective one-hot slot (10..14 in GOAL_FIELDS)."""
    mapping = {
        "traverse": 0,  # obj_navigate
        "acquire": 1,  # obj_pickup
        "objective": 2,  # obj_use_item
        "do_puzzle": 2,
        "trigger_cutscene": 2,
        "boss": 3,  # obj_fight
        "use_box": 4,  # obj_scripted
    }
    idx = mapping.get(str(op or ""))
    return idx


def _planner_step_target_xz(
    step: dict[str, Any],
    *,
    item_positions: Any | None = None,
) -> tuple[float, float] | None:
    """World XZ for the current planner step (pickup pile or story USE site)."""
    op = str(step.get("op") or "")
    if op == "acquire":
        pickup_id = str(step.get("pickup_id") or "")
        room, item, _pile = _pickup_id_parts(pickup_id)
        if item_positions is not None and room and item:
            pos = item_positions.get(room, item)
            if pos is not None:
                return float(pos[0]), float(pos[1])
        return None
    if op in {"objective", "do_puzzle", "trigger_cutscene", "boss"}:
        site_id = str(step.get("site_id") or "")
        if not site_id:
            return None
        from re1_rl.story_item_use import load_story_use_sites

        for site in load_story_use_sites():
            if str(site.get("id") or "") == site_id:
                return float(site["x"]), float(site["z"])
    return None


def encode_planner_loyal_goal(
    encoder: Any,
    graph: Any,
    state: dict[str, Any],
    queue: PlannerLoyalQueue,
    *,
    cell_time_remaining: float | None = None,
    item_positions: Any | None = None,
) -> np.ndarray:
    """Queue-driven goal vector: target room, hop distance, exit/in-room compass."""
    from re1_rl.obs_encoder import (
        CELL_TIME_REMAINING_INDEX,
        GOAL_DIM,
        GOAL_LOOKAHEAD_SLOT_DIM,
        GOAL_LOOKAHEAD_SLOTS,
        GOAL_BASE_DIM,
    )
    from re1_rl.memory_map import ITEM_IDS

    v = np.zeros(GOAL_DIM, dtype=np.float32)
    step = queue.current
    if step is None:
        remaining = 1.0 if cell_time_remaining is None else float(cell_time_remaining)
        v[CELL_TIME_REMAINING_INDEX] = float(np.clip(remaining, 0.0, 1.0))
        return v

    room = str(state.get("room_id", "") or "")
    target_room = queue.target_room()
    v[0] = encoder._room_idx_norm(target_room)
    hops = graph.hop_distance(room, target_room) if target_room else None
    v[3] = 1.0 if hops is None else min(float(hops) / 20.0, 1.0)
    v[4] = 1.0 if target_room is not None and room == str(target_room) else 0.0

    op = str(step.get("op") or "")
    compass_set = False
    if op == "traverse" and target_room is not None and room != str(target_room):
        door = graph.exit_toward(room, str(target_room))
        if door is not None:
            v[5:10] = encoder._compass_to_xz(state, float(door.x), float(door.z))
            v[21] = 1.0
            compass_set = True
    elif room and (target_room is None or room == str(target_room)):
        target_xz = _planner_step_target_xz(step, item_positions=item_positions)
        if target_xz is not None:
            v[5:10] = encoder._compass_to_xz(state, target_xz[0], target_xz[1])
            v[21] = 1.0
            compass_set = True
    if not compass_set and op == "traverse" and target_room is not None:
        door = graph.exit_toward(room, str(target_room))
        if door is not None:
            v[5:10] = encoder._compass_to_xz(state, float(door.x), float(door.z))
            v[21] = 1.0

    obj_idx = _planner_op_objective_index(op)
    if obj_idx is not None:
        v[10 + obj_idx] = 1.0

    # Semantic target item id in lookahead slot 0 (stable, not hash-only).
    base = GOAL_BASE_DIM
    slot = v[base : base + GOAL_LOOKAHEAD_SLOT_DIM]
    slot[0] = 1.0
    slot[1] = encoder._room_idx_norm(target_room or str(step.get("room_id") or room))
    if op == "acquire":
        _room, item, _pile = _pickup_id_parts(str(step.get("pickup_id") or ""))
        item_id = ITEM_IDS.get(item, 0)
        slot[12] = float(item_id) / 75.0  # MAX_ITEM_ID ~= 0x4B
    elif op in {"objective", "do_puzzle", "trigger_cutscene"}:
        site_id = str(step.get("site_id") or "")
        if site_id:
            digest = hashlib.md5(site_id.encode("utf-8")).digest()
            slot[12] = int.from_bytes(digest[:2], "big") / 65535.0

    remaining = 1.0 if cell_time_remaining is None else float(cell_time_remaining)
    v[CELL_TIME_REMAINING_INDEX] = float(np.clip(remaining, 0.0, 1.0))
    return v


def planner_loyal_enabled() -> bool:
    raw = os.environ.get("RE1_PLANNER_LOYAL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_chunk_path(project_root: Path | str | None = None) -> Path:
    raw = os.environ.get("RE1_PLANNER_CHUNK", "").strip()
    if not raw:
        return DEFAULT_CHUNK
    path = Path(raw)
    if path.is_absolute():
        return path
    root = Path(project_root) if project_root is not None else ROOT
    return root / path


def _route_admin_goal_indices() -> tuple[int, ...]:
    from re1_rl.obs_encoder import GOAL_FIELDS

    return tuple(
        i for i, (name, _) in enumerate(GOAL_FIELDS) if name in _ROUTE_ADMIN_GOAL_NAMES
    )


def prune_route_admin_goal(goal: Any) -> np.ndarray:
    """Zero route-admin scalars; leave compass / room / objective bits intact."""
    out = np.asarray(goal, dtype=np.float32).copy()
    if out.ndim == 0:
        return out
    for index in _route_admin_goal_indices():
        if index < out.shape[-1]:
            out[..., index] = 0.0
    return out


def apply_planner_loyal_obs(
    obs: dict[str, Any],
    queue: PlannerLoyalQueue | None,
) -> dict[str, Any]:
    """Attach ``planner_steps``, drop strategy/almanac keys, prune route-admin goals.

    Completed planner steps are already omitted from the encoding (queue pop /
    slide). No-op if queue is None (yawn rails / flag off).
    """
    if queue is None:
        return obs
    for key in PLANNER_LOYAL_OMIT_OBS_KEYS:
        obs.pop(key, None)
    obs["planner_steps"] = np.asarray(encode_planner_queue(queue), dtype=np.float32)
    if "goal" in obs:
        obs["goal"] = prune_route_admin_goal(obs["goal"])
    return obs
