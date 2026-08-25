"""Planner-loyal PPO: follow LLM-authored atomic steps; divert = terminal.

Reward contract (imperator 2026-08-25):
- Keep: stepwise contempt, damage taken, damage dealt, heal-from-HP rewards.
- Heal-use tax: green/blue herb -0.05; stronger heals -0.10.
- Completing the current planner step: +8 scaled by leftover 12m cell budget
  (``+8 * leftover_frac``); then rearm a fresh 12m wall for the next step.
- Divert (wrong room / unplanned pickup / unplanned box open): -4, episode end.
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
HEAL_USE_TAX_LIGHT = -0.05  # green / blue herb
HEAL_USE_TAX_STRONG = -0.10  # sprays, red mixes, multi-herb mixes

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

    def seek(self, index: int) -> None:
        """Jump queue to ``index`` (0 = first step; len(steps) = done)."""
        n = len(self._steps)
        self._index = max(0, min(int(index), n))
        self.step_success_pending = False
        self.divert_reason = None

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
        return str(step.get("room_id") or "") or None

    def evaluate_transition(
        self,
        *,
        prev_state: dict[str, Any],
        state: dict[str, Any],
        box_opened: bool = False,
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

        step = self.current or {}
        op = str(step.get("op") or "")
        prev_room = str(prev_state.get("room_id") or "")
        room = str(state.get("room_id") or "")

        # Heal-use tax from inventory disappearance of heal items (not box).
        tax = _heal_use_tax(prev_state, state)
        result["heal_use_tax"] = tax

        # Divert: unplanned box open.
        if box_opened and op != "use_box":
            result["divert"] = True
            result["divert_reason"] = "unplanned_box"
            self.divert_reason = result["divert_reason"]
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
            if op != "acquire":
                result["divert"] = True
                result["divert_reason"] = f"unplanned_pickup:{sorted(gained)}"
                self.divert_reason = result["divert_reason"]
                return result
            want = str(step.get("pickup_id") or "")
            want_item = want.split(":")[1] if want.count(":") >= 2 else want
            want_item = canonical_item(want_item)
            if want_item not in gained and want not in gained:
                # Soft: any gain of the expected item name counts.
                if want_item not in {canonical_item(x) for x in gained}:
                    result["divert"] = True
                    result["divert_reason"] = f"wrong_pickup want={want} got={sorted(gained)}"
                    self.divert_reason = result["divert_reason"]
                    return result
            result["step_success"] = True
            self._index += 1
            self.step_success_pending = True
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


def _inventory_name_counts(state: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in state.get("inventory_slots") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("item")
        elif isinstance(entry, (list, tuple)) and entry:
            name = entry[0]
        else:
            name = None
        if not name:
            continue
        key = canonical_item(str(name))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _inventory_gains(prev_state: dict[str, Any], state: dict[str, Any]) -> set[str]:
    prev = _inventory_name_counts(prev_state)
    cur = _inventory_name_counts(state)
    gained: set[str] = set()
    for name, count in cur.items():
        if count > prev.get(name, 0):
            gained.add(name)
    return gained


def _heal_use_tax(prev_state: dict[str, Any], state: dict[str, Any]) -> float:
    prev = _inventory_name_counts(prev_state)
    cur = _inventory_name_counts(state)
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
