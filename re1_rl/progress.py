"""Per-episode progress state: hysteresis + anti-farm bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProgressTracker:
    """Reset once per episode. Waypoint bonuses pay out only on NEW max
    progress (AllowBacktracking pattern) so door-loop farming yields nothing,
    while legitimate backtracking is never punished."""

    max_waypoint: int = 0
    rewarded_waypoint_indices: set[int] = field(default_factory=set)
    visited_rooms: set[str] = field(default_factory=set)
    visited_at_waypoint: dict[str, int] = field(default_factory=dict)
    visited_at_route_seq: dict[str, int] = field(default_factory=dict)
    penalized_offroute_rooms: set[str] = field(default_factory=set)
    rewarded_cutscenes: set[str] = field(default_factory=set)
    _success_room_rewarded: bool = False
    _in_control_steps: dict[str, int] = field(default_factory=dict)

    # Softlock anti-stall / anti-door-thrash (see reward.compute_reward).
    _stagnation_steps: int = 0
    _thrash_edge: frozenset[str] | None = None
    _thrash_transitions: int = 0

    def first_visit(
        self,
        room_id: str,
        *,
        at_waypoint: int = 0,
        at_route_seq: int | None = None,
    ) -> bool:
        room_id = str(room_id)
        new = room_id not in self.visited_rooms
        self.visited_rooms.add(room_id)
        if room_id not in self.visited_at_waypoint:
            self.visited_at_waypoint[room_id] = int(at_waypoint)
        if at_route_seq is not None and room_id not in self.visited_at_route_seq:
            self.visited_at_route_seq[room_id] = int(at_route_seq)
        return new

    def record_in_control_step(self, room_id: str, in_control: bool) -> None:
        if in_control:
            room_id = str(room_id)
            self._in_control_steps[room_id] = self._in_control_steps.get(room_id, 0) + 1

    def in_control_steps_in_room(self, room_id: str) -> int:
        return int(self._in_control_steps.get(str(room_id), 0))

    def on_waypoint_advanced(self) -> None:
        """Reset per-room step counters so repeated hall objectives work."""
        self._in_control_steps.clear()

    def note_softlock_step(
        self,
        *,
        room: str,
        prev_room: str,
        made_progress: bool,
        softlock_threshold: int,
        thrash_threshold: int,
    ) -> tuple[bool, bool]:
        """Update stagnation / door-thrash counters for one env step.

        Returns ``(stagnation_hit, thrash_hit)``.

        * Stagnation counts steps since the last progress event (new room,
          item, waypoint, or cutscene). Periodic hit every ``softlock_threshold``.
        * Thrash counts consecutive undirected room-edge oscillations (A↔B)
          without progress; periodic hit every ``thrash_threshold`` transitions.
        """
        if made_progress:
            self._stagnation_steps = 0
            self._thrash_edge = None
            self._thrash_transitions = 0
            return False, False

        self._stagnation_steps += 1
        stagnation_hit = (
            softlock_threshold > 0
            and self._stagnation_steps > 0
            and self._stagnation_steps % softlock_threshold == 0
        )

        thrash_hit = False
        room_s = str(room)
        prev_s = str(prev_room)
        if room_s and prev_s and room_s != prev_s:
            edge = frozenset({prev_s, room_s})
            if self._thrash_edge == edge:
                self._thrash_transitions += 1
            else:
                self._thrash_edge = edge
                self._thrash_transitions = 1
            thrash_hit = (
                thrash_threshold > 0
                and self._thrash_transitions > 0
                and self._thrash_transitions % thrash_threshold == 0
            )

        return stagnation_hit, thrash_hit

    def claim_waypoint_bonus(self, waypoint_index: int) -> bool:
        """True exactly once per waypoint index per episode."""
        if waypoint_index in self.rewarded_waypoint_indices:
            return False
        if waypoint_index < self.max_waypoint:
            return False
        self.max_waypoint = max(self.max_waypoint, waypoint_index)
        self.rewarded_waypoint_indices.add(waypoint_index)
        return True

    def claim_offroute_penalty(self, room_id: str) -> bool:
        """True only on first transition into a given off-route room."""
        if room_id in self.penalized_offroute_rooms:
            return False
        self.penalized_offroute_rooms.add(room_id)
        return True

    def claim_cutscene_bonus(self, cutscene_key: str) -> bool:
        """True once per distinct cutscene key per episode."""
        key = str(cutscene_key)
        if not key or key in self.rewarded_cutscenes:
            return False
        self.rewarded_cutscenes.add(key)
        return True

    def claim_success_room_bonus(self, room_id: str, success_room: str | None) -> bool:
        """True once per episode on first arrival in ``success_room``."""
        if not success_room or str(room_id) != str(success_room):
            return False
        if self._success_room_rewarded:
            return False
        self._success_room_rewarded = True
        return True

    @property
    def reached_success_room(self) -> bool:
        return self._success_room_rewarded
