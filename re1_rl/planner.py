"""Symbolic waypoint planner over the RE1 room graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class WaypointPlanner:
    """Tracks progress through a route JSON and per-stage waypoint list."""

    def __init__(
        self,
        route_path: str | Path,
        waypoints: list[str] | None = None,
        required_items: list[str] | None = None,
    ) -> None:
        self.route_path = Path(route_path)
        self.route: list[dict[str, Any]] = self._load_route()
        self._waypoint_ids: list[str] = [str(w) for w in (waypoints or [])]
        self._required_items: list[str] = list(required_items or [])
        self._index = 0

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

    def next_waypoint_room(self) -> str | None:
        if self._index >= len(self._waypoint_ids):
            return None
        return self._waypoint_ids[self._index]

    def required_items(self) -> list[str]:
        return list(self._required_items)

    def current_objective(self) -> dict[str, Any] | None:
        if self._index >= len(self.route):
            return None
        return self.route[self._index]

    def advance_if_success(self, state: dict[str, Any]) -> bool:
        """Advance waypoint index when success_condition matches state."""
        wp_room = self.next_waypoint_room()
        if wp_room is None:
            return False

        room = str(state.get("room_id", ""))
        if room != str(wp_room):
            return False

        step = self.current_objective() or {}
        cond = step.get("success_condition")
        if cond is None:
            self._index += 1
            return True

        if self._check_condition(cond, state):
            self._index += 1
            return True
        return False

    @staticmethod
    def _check_condition(cond: dict[str, Any], state: dict[str, Any]) -> bool:
        cond_type = cond.get("type", "room_enter")
        if cond_type == "room_enter":
            return str(state.get("room_id", "")) == str(cond.get("room_id", ""))
        if cond_type == "has_item":
            inv = set(state.get("inventory", []))
            return cond.get("item") in inv
        return False

    @property
    def waypoint_index(self) -> int:
        return self._index
