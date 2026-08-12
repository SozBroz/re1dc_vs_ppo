"""Symbolic waypoint planner over the RE1 room graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from re1_rl.item_todo import canonical_item
from re1_rl.key_items import KEY_ITEM_NAMES
from re1_rl.memory_map import ITEM_IDS, WEAPON_ITEM_IDS

if TYPE_CHECKING:
    from re1_rl.progress import ProgressTracker

_KEY_ITEM_NAME_SET: frozenset[str] = frozenset(KEY_ITEM_NAMES)
_WEAPON_NAME_SET: frozenset[str] = frozenset(
    ITEM_IDS[i] for i in WEAPON_ITEM_IDS if i in ITEM_IDS
)


def _is_key_or_weapon(name: str) -> bool:
    return name in _KEY_ITEM_NAME_SET or name in _WEAPON_NAME_SET

# Order matters: index in this tuple = position in the objective one-hot.
OBJECTIVE_TYPES = ("navigate", "pickup", "use_item", "fight", "scripted_macro")


class WaypointPlanner:
    """Tracks progress through a route JSON and per-stage waypoint list."""

    def __init__(
        self,
        route_path: str | Path,
        waypoints: list[str] | None = None,
        route_steps: list[int] | None = None,
        required_items: list[str] | None = None,
        terminal_goal_room: str | None = None,
        start_index: int = 0,
    ) -> None:
        self.route_path = Path(route_path)
        self.route: list[dict[str, Any]] = self._load_route()
        self._route_step_seqs: list[int] = [int(s) for s in (route_steps or [])]
        self._required_items: list[str] = list(required_items or [])
        self._terminal_goal_room = str(terminal_goal_room) if terminal_goal_room else None
        self._index = max(0, int(start_index))

        # Explicit route_steps (including []) wins: empty list = no waypoints.
        # Only the legacy None path falls back to the full route JSON.
        if route_steps is not None:
            self._waypoint_ids = [
                str(self.step_by_seq(seq).get("room_id", ""))
                for seq in self._route_step_seqs
            ]
        else:
            self._waypoint_ids = [str(w) for w in (waypoints or [])]
            if not self._waypoint_ids and self.route:
                self._waypoint_ids = [str(step.get("room_id", "")) for step in self.route]

    def _load_route(self) -> list[dict[str, Any]]:
        if not self.route_path.is_file():
            return []
        try:
            with self.route_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, list):
            return data
        return data.get("waypoints", data.get("route", []))

    def step_by_seq(self, seq: int) -> dict[str, Any]:
        for step in self.route:
            if int(step.get("seq", 0)) == int(seq):
                return step
        return {}

    def next_waypoint_room(self) -> str | None:
        if self._index >= len(self._waypoint_ids):
            return self._terminal_goal_room
        return self._waypoint_ids[self._index]

    def required_items(self) -> list[str]:
        step = self.current_objective() or {}
        req = list(self._required_items)
        req.extend(step.get("required_items", []))
        return req

    def current_objective(self) -> dict[str, Any] | None:
        return self.peek_objective(0)

    def peek_objective(self, offset: int = 0) -> dict[str, Any] | None:
        """Return a route objective relative to the active checkpoint."""
        index = self._index + int(offset)
        if index < 0:
            return None
        if self._route_step_seqs:
            if index >= len(self._route_step_seqs):
                return None
            return self.step_by_seq(self._route_step_seqs[index])
        if index >= len(self._waypoint_ids):
            return None
        wp_room = self._waypoint_ids[index]
        for step in self.route:
            if str(step.get("room_id", "")) == str(wp_room):
                return step
        return None

    def peek_waypoint_room(self, offset: int = 0) -> str | None:
        """Return a checkpoint room relative to the active checkpoint."""
        index = self._index + int(offset)
        if index < 0 or index >= len(self._waypoint_ids):
            return None
        return self._waypoint_ids[index]

    def peek_objective_type(self, offset: int = 0) -> str:
        """Return a normalized action type for a future checkpoint."""
        step = self.peek_objective(offset)
        if step is None:
            return "navigate"
        action_type = step.get("action_type", "navigate")
        return action_type if action_type in OBJECTIVE_TYPES else "navigate"

    def peek_required_items(self, offset: int = 0) -> list[str]:
        """Return stage plus checkpoint prerequisites for lookahead encoding."""
        step = self.peek_objective(offset)
        if step is None:
            return []
        return [*self._required_items, *step.get("required_items", [])]

    def peek_items_gained(self, offset: int = 0) -> list[str]:
        """Return declared acquisitions for a future checkpoint."""
        step = self.peek_objective(offset)
        return list(step.get("items_gained", [])) if step is not None else []

    @property
    def waypoints_remaining(self) -> int:
        return max(0, self.total_waypoints - self._index)

    def current_route_seq(self) -> int | None:
        if self._route_step_seqs and self._index < len(self._route_step_seqs):
            return int(self._route_step_seqs[self._index])
        return None

    def objective_at(self, index: int) -> dict[str, Any] | None:
        """Return the route objective at an absolute waypoint index."""
        idx = int(index)
        if idx < 0:
            return None
        if self._route_step_seqs:
            if idx >= len(self._route_step_seqs):
                return None
            return self.step_by_seq(self._route_step_seqs[idx])
        if idx >= len(self._waypoint_ids):
            return None
        wp_room = self._waypoint_ids[idx]
        for step in self.route:
            if str(step.get("room_id", "")) == str(wp_room):
                return step
        return None

    def index_of_item_gain_checkpoint(self, item_name: str) -> int | None:
        """First waypoint index whose ``items_gained`` includes ``item_name``."""
        want = canonical_item(str(item_name))
        if not want:
            return None
        for i in range(self.total_waypoints):
            step = self.objective_at(i) or {}
            gains = {
                canonical_item(str(x))
                for x in (step.get("items_gained") or [])
            }
            if want in gains:
                return i
        return None

    def advance_if_success(
        self,
        state: dict[str, Any],
        *,
        progress: ProgressTracker | None = None,
        prev_state: dict[str, Any] | None = None,
    ) -> bool:
        """Advance waypoint index when the route step's success_condition matches."""
        wp_room = self.next_waypoint_room()
        if wp_room is None:
            return False

        step = self.current_objective() or {}
        cond = step.get("success_condition")
        if progress is not None and prev_state is not None:
            progress.note_leg_room_transition(
                str(prev_state.get("room_id", "")),
                str(state.get("room_id", "")),
            )

        if self._condition_met(cond, state, str(wp_room), progress, prev_state):
            self._index += 1
            return True
        return False

    @staticmethod
    def _condition_met(
        cond: Any,
        state: dict[str, Any],
        wp_room: str,
        progress: ProgressTracker | None,
        prev_state: dict[str, Any] | None,
    ) -> bool:
        if cond is None:
            return str(state.get("room_id", "")) == wp_room
        if isinstance(cond, str):
            return str(state.get("room_id", "")) == wp_room if cond.strip() else True
        if not isinstance(cond, dict):
            return False

        cond_type = cond.get("type", "room_enter")

        if cond_type == "room_enter_any":
            room = str(state.get("room_id", ""))
            allowed = {str(r) for r in cond.get("room_ids", [])}
            return room in allowed

        if cond_type == "any_of":
            subs = cond.get("conditions", [])
            return any(
                WaypointPlanner._condition_met(sub, state, wp_room, progress, prev_state)
                for sub in subs
            )

        if cond_type == "all_of":
            subs = cond.get("conditions", [])
            return bool(subs) and all(
                WaypointPlanner._condition_met(sub, state, wp_room, progress, prev_state)
                for sub in subs
            )

        if cond_type == "visited_any":
            if progress is None:
                return False
            allowed = {str(r) for r in cond.get("room_ids", [])}
            min_seq = int(cond.get("min_route_seq", cond.get("min_waypoint_index", 0)))
            for room_id in allowed:
                if room_id not in progress.visited_rooms:
                    continue
                if progress.visited_at_route_seq.get(room_id, 0) >= min_seq:
                    return True
            return False

        if cond_type == "room_enter_from":
            target = str(cond.get("room_id", wp_room))
            from_ids = {str(r) for r in cond.get("from_room_ids", [])}
            immediate = (
                prev_state is not None
                and str(state.get("room_id", "")) == target
                and str(prev_state.get("room_id", "")) in from_ids
            )
            return immediate or (
                progress is not None
                and str(state.get("room_id", "")) == target
                and progress.leg_entered_from(target, from_ids)
            )

        if cond_type == "observed_cutscene":
            if progress is None:
                return False
            prefix = str(cond.get("prefix", ""))
            return bool(prefix) and any(
                str(key).startswith(prefix) for key in progress.observed_cutscenes
            )

        if cond_type == "yawn_box_prep_exit":
            from re1_rl.yawn_box_prep_checkpoint import yawn_box_prep_exit_met

            return yawn_box_prep_exit_met(state, prev_state, progress)

        if cond_type == "has_item":
            # Inventory checks are room-agnostic (cp25 Barry rescue ends in 109).
            return WaypointPlanner._check_in_room_condition(
                cond, state, wp_room, progress
            )

        if cond_type in (
            "lacks_item",
            "item_in_box",
            "yawn_box_weapon_ammo_clear",
        ):
            return WaypointPlanner._check_in_room_condition(
                cond, state, wp_room, progress
            )

        if cond_type == "state_flag" and str(cond.get("field", "")) == "lab_timer":
            return WaypointPlanner._check_in_room_condition(
                cond, state, wp_room, progress
            )

        if cond_type == "leg_kills_in_room":
            if progress is None:
                return False
            room = str(cond.get("room_id", "")).upper()
            min_kills = int(cond.get("min_kills", 1))
            kills = getattr(progress, "leg_kills_by_room", None) or {}
            return bool(room) and int(kills.get(room, 0)) >= min_kills

        if str(state.get("room_id", "")) != wp_room:
            return False

        return WaypointPlanner._check_in_room_condition(cond, state, wp_room, progress)

    @staticmethod
    def _check_in_room_condition(
        cond: dict[str, Any],
        state: dict[str, Any],
        wp_room: str,
        progress: ProgressTracker | None,
    ) -> bool:
        cond_type = cond.get("type", "room_enter")
        if cond_type == "room_enter":
            return str(state.get("room_id", "")) == str(cond.get("room_id", wp_room))
        if cond_type == "has_item":
            inv = {canonical_item(str(x)) for x in state.get("inventory", [])}
            want = canonical_item(str(cond.get("item", "")))
            return bool(want) and want in inv
        if cond_type == "lacks_item":
            inv = {canonical_item(str(x)) for x in state.get("inventory", [])}
            want = canonical_item(str(cond.get("item", "")))
            return bool(want) and want not in inv
        if cond_type == "item_in_box":
            from re1_rl.item_box import BOX_SLOTS_LIVE
            from re1_rl.yawn_box_prep_checkpoint import box_has_item, box_pairs_from_state

            want = canonical_item(str(cond.get("item", "")))
            max_slot = int(cond.get("max_slot", BOX_SLOTS_LIVE - 1))
            return bool(want) and box_has_item(
                box_pairs_from_state(state),
                want,
                max_slot=max_slot,
            )
        if cond_type == "yawn_box_weapon_ammo_clear":
            from re1_rl.yawn_box_prep_checkpoint import (
                box_pairs_from_state,
                yawn_box_weapon_ammo_clear,
            )

            return yawn_box_weapon_ammo_clear(box_pairs_from_state(state))
        if cond_type == "acquired_item":
            want = canonical_item(str(cond.get("item", "")))
            if not want:
                return False
            # This-leg pickup always counts (ammo / ink / junk stay leg-only).
            if progress is not None and want in progress.leg_acquired_items:
                return True
            # Key items + weapons: already held / earlier acquisition passes so
            # savestate cells and delayed settles are not softlocked.
            if not _is_key_or_weapon(want):
                return False
            inv = {canonical_item(str(x)) for x in state.get("inventory", [])}
            if want in inv:
                return True
            if progress is None:
                return False
            return (
                want in progress.key_items_rewarded
                or progress.weapon_progress_claimed(want)
            )
        if cond_type == "story_use":
            if progress is None:
                return False
            site = str(cond.get("site_id", ""))
            return bool(site) and site in progress.rewarded_story_uses
        if cond_type == "observed_cutscene":
            if progress is None:
                return False
            prefix = str(cond.get("prefix", ""))
            return bool(prefix) and any(
                str(key).startswith(prefix) for key in progress.observed_cutscenes
            )
        if cond_type == "typewriter_save":
            return bool(state.get("typewriter_save_complete"))
        if cond_type == "state_flag":
            field = str(cond.get("field", ""))
            return bool(field) and state.get(field) == cond.get("value", True)
        if cond_type == "in_control_steps_in_room":
            if progress is None:
                return False
            target_room = str(cond.get("room_id", wp_room))
            if str(state.get("room_id", "")) != target_room:
                return False
            min_steps = int(cond.get("min_steps", 1))
            return progress.in_control_steps_in_room(target_room) >= min_steps
        if cond_type == "in_control_steps_since_cutscene":
            if progress is None:
                return False
            prefix = str(cond.get("prefix", ""))
            min_steps = int(cond.get("min_steps", 1))
            target_room = str(cond.get("room_id", wp_room) or wp_room)
            if target_room and str(state.get("room_id", "")) != target_room:
                return False
            return bool(prefix) and (
                progress.in_control_steps_since_cutscene(prefix) >= min_steps
            )
        if cond_type == "gallery_progress":
            from re1_rl.gallery_puzzle import completed_steps

            min_steps = int(cond.get("min_steps", 1))
            raw = int(state.get("gallery_progress", 0) or 0)
            return completed_steps(raw) >= min_steps
        return False

    @property
    def waypoint_index(self) -> int:
        return self._index

    @property
    def total_waypoints(self) -> int:
        return len(self._waypoint_ids)

    def objective_type(self) -> str:
        """action_type of the current route step, defaulting to 'navigate'."""
        step = self.current_objective()
        if step is None:
            return "navigate"
        at = step.get("action_type", "navigate")
        return at if at in OBJECTIVE_TYPES else "navigate"

    def objective_one_hot(self) -> np.ndarray:
        vec = np.zeros(len(OBJECTIVE_TYPES), dtype=np.float32)
        vec[OBJECTIVE_TYPES.index(self.objective_type())] = 1.0
        return vec
