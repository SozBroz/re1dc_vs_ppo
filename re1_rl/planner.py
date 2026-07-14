"""Symbolic waypoint planner over the RE1 room graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from re1_rl.item_todo import canonical_item

if TYPE_CHECKING:
    from re1_rl.progress import ProgressTracker

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
    ) -> None:
        self.route_path = Path(route_path)
        self.route: list[dict[str, Any]] = self._load_route()
        self._route_step_seqs: list[int] = [int(s) for s in (route_steps or [])]
        self._required_items: list[str] = list(required_items or [])
        self._terminal_goal_room = str(terminal_goal_room) if terminal_goal_room else None
        self._index = 0

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
        if self._route_step_seqs:
            if self._index >= len(self._route_step_seqs):
                return None
            return self.step_by_seq(self._route_step_seqs[self._index])
        wp_room = self.next_waypoint_room()
        if wp_room is None:
            return None
        for step in self.route:
            if str(step.get("room_id", "")) == str(wp_room):
                return step
        return None

    def current_route_seq(self) -> int | None:
        if self._route_step_seqs and self._index < len(self._route_step_seqs):
            return int(self._route_step_seqs[self._index])
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
            if prev_state is None:
                return False
            target = str(cond.get("room_id", wp_room))
            from_ids = {str(r) for r in cond.get("from_room_ids", [])}
            return (
                str(state.get("room_id", "")) == target
                and str(prev_state.get("room_id", "")) in from_ids
            )

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
        if cond_type == "in_control_steps_in_room":
            if progress is None:
                return False
            target_room = str(cond.get("room_id", wp_room))
            if str(state.get("room_id", "")) != target_room:
                return False
            min_steps = int(cond.get("min_steps", 1))
            return progress.in_control_steps_in_room(target_room) >= min_steps
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
