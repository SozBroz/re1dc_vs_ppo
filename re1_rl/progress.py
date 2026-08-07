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
    # Every qualified freeze observed this episode, whether or not it paid.
    # Paid cutscenes remain a strict subset in ``rewarded_cutscenes``.
    observed_cutscenes: set[str] = field(default_factory=set)
    rewarded_story_uses: set[str] = field(default_factory=set)
    # First rising edge into document/file examine UI per room this episode.
    # No stable document ID in RAM yet — room key matches new_room anti-farm.
    rewarded_document_rooms: set[str] = field(default_factory=set)
    # After key/weapon pickup: suppress same-room cutscene fragments until leave.
    # Implements skill (a) for multi-settle pickup cinema (emblem grab → +1s).
    cutscene_blocked_after_pickup_room: str | None = None
    _success_room_rewarded: bool = False
    _in_control_steps: dict[str, int] = field(default_factory=dict)
    # In-control steps after each newly observed cutscene key (post-cinema settle).
    _in_control_since_cutscene: dict[str, int] = field(default_factory=dict)

    # Idle contempt: emulated frames since last exploration progress (reward.compute_reward).
    _stagnation_frames: int = 0
    # Floor on softlock truncate after progress / rails checkpoint (12m chunks).
    softlock_cap_frames: int = 0
    # Additive hard ``max_steps`` budget beyond curriculum base (12m per cell).
    max_steps_bonus: int = 0
    # Terminal black mark: set before building the terminal observation.
    # Positive reward/extension guards also prevent same-step leakage.
    kenneth_gate_breached: bool = False
    # Rails off-path: wrong_room penalty ends the episode (like Kenneth gate).
    wrong_room_breached: bool = False
    # Spawn room (usually dining 105): visited at reset; no +new_room payout —
    # fresh start matches archive/PB sidecars that already carry spawn credit.
    spawn_room_id: str | None = None
    _spawn_room_bonus_paid: bool = False
    # Weapon names that already granted idle-extend / stagnation reset this ep.
    # Shotgun rack re-takes still pay ±NEW_WEAPON but cannot re-farm the clock.
    weapons_progressed: set[str] = field(default_factory=set)
    # Key items that already paid KEY_ITEM_PICKUP_BONUS this episode.
    key_items_rewarded: set[str] = field(default_factory=set)
    # Async skip may present one inventory transition twice. A wall return pays
    # once, then cannot pay again until shotgun possession is observed.
    _shotgun_return_armed: bool | None = None
    gallery_step_index: int = 0
    gallery_pending_reward: float = 0.0
    gallery_completed: bool = False
    gallery_puzzle_solved: bool = False
    gallery_needs_reentry: bool = False
    gallery_wrong_breached: bool = False
    dining_statue_rewarded: bool = False
    # Pickups made after the current rails checkpoint. For non-key/non-weapon
    # items, only this set satisfies ``acquired_item``. Key items and weapons
    # also accept current inventory / earlier episode reward bookkeeping.
    leg_acquired_items: set[str] = field(default_factory=set)
    # Directed room transitions observed during the current rails leg. This
    # lets a checkpoint combine a valid entry with an event that settles a few
    # steps later without accepting an entry from a different room.
    leg_room_transitions: set[tuple[str, str]] = field(default_factory=set)
    leg_span: int = 1
    legs_completed: int = 0
    checkpoint_success: bool = False
    # Semantic box-departure state persists until next box, boss outcome, death,
    # or truncation; rollout boundaries do not reset ProgressTracker.
    loadout_segment: dict | None = None
    pending_loadout_sample: dict | None = None

    def seed_spawn_room(self, room_id: str) -> None:
        """Mark spawn visited; consume spawn credit (no ``new_room`` payout)."""
        room = str(room_id or "")
        self.spawn_room_id = room or None
        self._spawn_room_bonus_paid = True
        if room:
            self.first_visit(room)

    def claim_spawn_room_bonus(self) -> bool:
        """Legacy no-op: spawn room credit is consumed in ``seed_spawn_room``."""
        if self._spawn_room_bonus_paid or not self.spawn_room_id:
            return False
        self._spawn_room_bonus_paid = True
        return False

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
            # Post-cutscene settle clocks (see in_control_steps_since_cutscene).
            for key in list(self._in_control_since_cutscene.keys()):
                self._in_control_since_cutscene[key] = (
                    int(self._in_control_since_cutscene[key]) + 1
                )

    def in_control_steps_in_room(self, room_id: str) -> int:
        return int(self._in_control_steps.get(str(room_id), 0))

    def in_control_steps_since_cutscene(self, prefix: str) -> int:
        """Max in-control steps since any observed cutscene matching ``prefix``."""
        prefix = str(prefix or "")
        if not prefix:
            return 0
        best = 0
        found = False
        for key, steps in self._in_control_since_cutscene.items():
            if str(key).startswith(prefix):
                found = True
                best = max(best, int(steps))
        return best if found else 0

    def on_waypoint_advanced(self) -> None:
        """Reset per-room step counters so repeated hall objectives work."""
        self._in_control_steps.clear()
        self._in_control_since_cutscene.clear()
        self.leg_room_transitions.clear()

    def note_leg_room_transition(self, from_room: str, to_room: str) -> None:
        source = str(from_room or "")
        target = str(to_room or "")
        if source and target and source != target:
            self.leg_room_transitions.add((source, target))

    def leg_entered_from(self, target_room: str, from_rooms: set[str]) -> bool:
        target = str(target_room or "")
        return any((str(source), target) in self.leg_room_transitions for source in from_rooms)

    @property
    def stagnation_frames(self) -> int:
        return int(self._stagnation_frames)

    def note_softlock_extension(self, frames: int) -> None:
        """Raise idle truncate floor and clear the idle clock (12m chunks)."""
        if self.kenneth_gate_breached:
            return
        frames = max(0, int(frames))
        if frames <= 0:
            return
        self.softlock_cap_frames = max(int(self.softlock_cap_frames), frames)
        self._stagnation_frames = 0

    def note_max_steps_extension(self, steps: int) -> None:
        """Add hard episode-step budget (used when a rails checkpoint completes)."""
        if self.kenneth_gate_breached:
            return
        steps = max(0, int(steps))
        if steps <= 0:
            return
        self.max_steps_bonus = int(self.max_steps_bonus) + steps

    def breach_kenneth_gate(self) -> bool:
        """Set the terminal black mark; true only on the first breach."""
        if self.kenneth_gate_breached:
            return False
        self.kenneth_gate_breached = True
        self.softlock_cap_frames = 0
        return True

    def note_stagnation_step(
        self,
        *,
        made_progress: bool,
        step_frames: int = 8,
    ) -> None:
        """Advance idle clock when no exploration progress this step.

        Progress is defined in ``compute_reward``: new room, document examine,
        new cutscene, new key item, first weapon acquire this episode, story
        use, or gallery, or dining statue knocked. Revisiting rooms, reopening a paid document room,
        junk pickups, and shotgun rack re-takes do not reset.
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

    def claim_weapon_progress(self, weapon_name: str) -> bool:
        """True once per weapon name per episode (idle extend / stagnation)."""
        name = str(weapon_name or "")
        if not name or name in self.weapons_progressed:
            return False
        self.weapons_progressed.add(name)
        return True

    def claim_key_item_bonus(self, item_name: str) -> bool:
        """True once per key-item name per episode (+4 pickup channel)."""
        name = str(item_name or "")
        if not name or name in self.key_items_rewarded:
            return False
        self.key_items_rewarded.add(name)
        return True

    def release_key_item_reward(self, item_name: str) -> bool:
        """Allow a future pickup reward after a put-back clawback."""
        name = str(item_name or "")
        if not name or name not in self.key_items_rewarded:
            return False
        self.key_items_rewarded.discard(name)
        return True

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

    def breach_wrong_room(self) -> bool:
        """Mark post-L Passage room detour as terminal; true only on first breach."""
        if self.wrong_room_breached:
            return False
        self.wrong_room_breached = True
        self.softlock_cap_frames = 0
        return True

    def claim_cutscene_bonus(self, cutscene_key: str) -> bool:
        """True once per distinct cutscene key per episode."""
        key = str(cutscene_key)
        if not key or key in self.rewarded_cutscenes:
            return False
        self.rewarded_cutscenes.add(key)
        return True

    def observe_cutscene(self, cutscene_key: str) -> bool:
        """Record a qualified freeze without implying that it paid."""
        key = str(cutscene_key)
        if not key or key in self.observed_cutscenes:
            return False
        self.observed_cutscenes.add(key)
        # Start post-cutscene settle clock at 0 (pre-cinema room dwell must not count).
        self._in_control_since_cutscene[key] = 0
        return True

    def note_leg_acquired(self, item_name: str) -> None:
        name = str(item_name or "")
        if name:
            self.leg_acquired_items.add(name)

    def claim_checkpoint_success(self) -> bool:
        if self.checkpoint_success:
            return False
        self.legs_completed += 1
        self.leg_acquired_items.clear()
        self.checkpoint_success = self.legs_completed >= max(1, int(self.leg_span))
        return True

    def begin_loadout_segment(
        self,
        features: list[float],
        *,
        waypoint_index: int,
        horizon_checkpoints: int,
        departure_room: str,
        departure_inventory: list[tuple[str, int]] | None = None,
    ) -> None:
        self.loadout_segment = {
            "features": [float(x) for x in features],
            "waypoint_index": int(waypoint_index),
            "horizon_checkpoints": max(1, int(horizon_checkpoints)),
            "departure_room": str(departure_room),
            "departure_inventory": list(departure_inventory or []),
        }

    def finish_loadout_segment(
        self,
        *,
        waypoint_index: int,
        survived: bool,
        completed: bool,
        outcome: str,
    ) -> dict | None:
        segment = self.loadout_segment
        if segment is None:
            return None
        progressed = max(0, int(waypoint_index) - int(segment["waypoint_index"]))
        progress = min(1.0, progressed / float(segment["horizon_checkpoints"]))
        sample = {
            **segment,
            "labels": [
                1.0 if completed else 0.0,
                1.0 if survived else 0.0,
                float(progress),
            ],
            "outcome": str(outcome),
            "end_waypoint_index": int(waypoint_index),
        }
        self.loadout_segment = None
        self.pending_loadout_sample = sample
        return sample

    def pop_loadout_sample(self) -> dict | None:
        sample = self.pending_loadout_sample
        self.pending_loadout_sample = None
        return sample

    def claim_document_examine_bonus(self, room_id: str) -> bool:
        """True once per room on first document-examine edge this episode."""
        room = str(room_id or "")
        if not room or room in self.rewarded_document_rooms:
            return False
        self.rewarded_document_rooms.add(room)
        return True

    def note_pickup_cutscene_block(self, room_id: str) -> None:
        """Arm same-room cutscene suppress after a key/weapon pickup."""
        room = str(room_id or "")
        self.cutscene_blocked_after_pickup_room = room or None

    def clear_pickup_cutscene_block_if_left(self, room_id: str) -> None:
        """Drop suppress once Jill leaves the pickup room."""
        blocked = self.cutscene_blocked_after_pickup_room
        if not blocked:
            return
        if str(room_id or "") != str(blocked):
            self.cutscene_blocked_after_pickup_room = None

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
    ) -> tuple[float, float]:
        """Pay ordered Gallery steps; claw back partial rewards on wrong portrait.

        Returns ``(gallery_pay, gallery_wrong_penalty)``. Wrong portrait pays
        clawback on pending steps plus ``-GALLERY_WRONG_PORTRAIT_PENALTY`` and
        marks the episode terminal via ``breach_gallery_wrong()``.
        """
        from re1_rl.gallery_puzzle import (
            GALLERY_COMPLETE_PREV_RAW,
            GALLERY_ROOM_ID,
            GALLERY_STEP_CLAWBACK_SCALE,
            GALLERY_STEP_REWARD,
            GALLERY_STEP_VALUES,
            GALLERY_WRONG_PORTRAIT_PENALTY,
            completed_steps,
        )

        if self.gallery_completed:
            return 0.0, 0.0
        if star_crest_held:
            self.gallery_completed = True
            self.gallery_puzzle_solved = True
            self.gallery_pending_reward = 0.0
            return 0.0, 0.0

        entered = str(prev_room) != GALLERY_ROOM_ID and str(room) == GALLERY_ROOM_ID
        left = str(prev_room) == GALLERY_ROOM_ID and str(room) != GALLERY_ROOM_ID
        if entered and self.gallery_needs_reentry:
            self.gallery_needs_reentry = False
            self.gallery_step_index = completed_steps(raw)
            self.gallery_pending_reward = 0.0
            return 0.0, 0.0

        if left:
            clawback = -self.gallery_pending_reward * GALLERY_STEP_CLAWBACK_SCALE
            self.gallery_needs_reentry = True
            self.gallery_step_index = 0
            self.gallery_pending_reward = 0.0
            return clawback, 0.0
        if str(room) != GALLERY_ROOM_ID:
            return 0.0, 0.0
        if self.gallery_needs_reentry:
            return 0.0, 0.0

        prev_count = completed_steps(prev_raw)
        count = completed_steps(raw)
        if self.gallery_step_index == 0 and self.gallery_pending_reward == 0.0:
            self.gallery_step_index = prev_count

        if int(raw) != int(prev_raw) and count == self.gallery_step_index + 1:
            self.gallery_step_index = count
            self.gallery_pending_reward += GALLERY_STEP_REWARD
            return GALLERY_STEP_REWARD, 0.0

        awaiting_final = (
            self.gallery_step_index >= len(GALLERY_STEP_VALUES)
            or int(prev_raw) == GALLERY_COMPLETE_PREV_RAW
        )
        if awaiting_final and int(raw) == 0:
            self.gallery_puzzle_solved = True
            self.gallery_step_index = len(GALLERY_STEP_VALUES)
            return 0.0, 0.0

        if self.gallery_puzzle_solved:
            return 0.0, 0.0

        if awaiting_final:
            # Final-switch / crest-reveal transients (e.g. 2→1) before RAM settles.
            return 0.0, 0.0

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
            clawback = -self.gallery_pending_reward * GALLERY_STEP_CLAWBACK_SCALE
            self.gallery_step_index = 0
            self.gallery_pending_reward = 0.0
            self.gallery_needs_reentry = True
            self.breach_gallery_wrong()
            return clawback, -GALLERY_WRONG_PORTRAIT_PENALTY
        return 0.0, 0.0

    def breach_gallery_wrong(self) -> bool:
        """Mark wrong gallery portrait as terminal; true only on first breach."""
        if self.gallery_wrong_breached:
            return False
        self.gallery_wrong_breached = True
        self.softlock_cap_frames = 0
        return True

    def claim_dining_statue_bonus(
        self,
        *,
        knocked: bool,
        prev_knocked: bool,
        room_id: str | int = "",
    ) -> bool:
        """True once per episode on rising edge of dining statue flag in room 202."""
        if self.dining_statue_rewarded:
            return False
        room = str(room_id).strip().upper()
        if room != "202":
            return False
        if knocked and not prev_knocked:
            self.dining_statue_rewarded = True
            return True
        return False

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
