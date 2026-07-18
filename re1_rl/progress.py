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
    rewarded_story_uses: set[str] = field(default_factory=set)
    _success_room_rewarded: bool = False
    _in_control_steps: dict[str, int] = field(default_factory=dict)

    # Idle contempt: emulated frames since last exploration progress (reward.compute_reward).
    _stagnation_frames: int = 0
    # Async skip may present one inventory transition twice. A wall return pays
    # once, then cannot pay again until shotgun possession is observed.
    _shotgun_return_armed: bool | None = None
    gallery_step_index: int = 0
    gallery_pending_reward: float = 0.0
    gallery_completed: bool = False
    gallery_needs_reentry: bool = False

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

    @property
    def stagnation_frames(self) -> int:
        return int(self._stagnation_frames)

    def note_stagnation_step(
        self,
        *,
        made_progress: bool,
        step_frames: int = 8,
    ) -> None:
        """Advance idle clock when no exploration progress this step.

        Progress is defined in ``compute_reward``: new room, new cutscene,
        new key item, or new weapon this step. Revisiting rooms or junk
        pickups do not reset.
        Each env step advances stagnation by ``step_frames`` (macro steps count more).
        """
        if made_progress:
            self._stagnation_frames = 0
            return
        self._stagnation_frames += max(int(step_frames), 0)

    def stagnation_timed_out(self, *, threshold: int) -> bool:
        """True once emulated idle frames reach the episode timeout threshold."""
        if threshold <= 0:
            return False
        return self._stagnation_frames >= int(threshold)

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

    def claim_story_use_bonus(self, site_id: str) -> bool:
        """True once per story USE site id per episode."""
        key = str(site_id)
        if not key or key in self.rewarded_story_uses:
            return False
        self.rewarded_story_uses.add(key)
        return True

    def gallery_step_reward(
        self,
        *,
        prev_room: str,
        room: str,
        prev_raw: int,
        raw: int,
        prev_confirm: int,
        confirm: int,
        star_crest_held: bool,
    ) -> float:
        """Pay ordered Gallery steps; claw back partial rewards on reset/exit."""
        from re1_rl.gallery_puzzle import (
            GALLERY_ROOM_ID,
            GALLERY_STEP_REWARD,
            completed_steps,
        )

        if self.gallery_completed:
            return 0.0
        if star_crest_held:
            self.gallery_completed = True
            self.gallery_pending_reward = 0.0
            return 0.0

        entered = str(prev_room) != GALLERY_ROOM_ID and str(room) == GALLERY_ROOM_ID
        left = str(prev_room) == GALLERY_ROOM_ID and str(room) != GALLERY_ROOM_ID
        if entered and self.gallery_needs_reentry:
            self.gallery_needs_reentry = False
            self.gallery_step_index = completed_steps(raw)
            self.gallery_pending_reward = 0.0
            return 0.0

        if left:
            clawback = -self.gallery_pending_reward
            self.gallery_needs_reentry = True
            self.gallery_step_index = 0
            self.gallery_pending_reward = 0.0
            return clawback
        if str(room) != GALLERY_ROOM_ID:
            return 0.0
        if self.gallery_needs_reentry:
            return 0.0

        prev_count = completed_steps(prev_raw)
        count = completed_steps(raw)
        if self.gallery_step_index == 0 and self.gallery_pending_reward == 0.0:
            self.gallery_step_index = prev_count

        if int(raw) != int(prev_raw) and count == self.gallery_step_index + 1:
            self.gallery_step_index = count
            self.gallery_pending_reward += GALLERY_STEP_REWARD
            return GALLERY_STEP_REWARD

        wrong_reset = int(raw) == 0 and int(prev_raw) != 0
        wrong_first = (
            int(raw) == 0
            and int(prev_raw) == 0
            and int(confirm) != int(prev_confirm)
            and int(confirm) != 0
        )
        unexpected_transition = (
            int(raw) != int(prev_raw) and count != self.gallery_step_index
        )
        if wrong_reset or wrong_first or unexpected_transition:
            clawback = -self.gallery_pending_reward
            self.gallery_step_index = 0
            self.gallery_pending_reward = 0.0
            self.gallery_needs_reentry = True
            return clawback
        return 0.0

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
