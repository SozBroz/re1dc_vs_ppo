"""Episode-local progress history for privileged obs (north star § history).

Ordered milestones only — not a scripted route compass.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from re1_rl.memory_map import ITEM_IDS

MAX_ITEM_ID = 0x4B  # highest mixed-herb id; keep in sync with spatial_encoder

ROOM_DEQUE_K = 32
# valid_fraction + K × (room_index_norm, steps_since_norm)
ROOM_HISTORY_DIM = 1 + ROOM_DEQUE_K * 2

# Cover most of a Jill Standard run's pickup sequence (catalog has 121 rows).
# Pair with keys_held / future pickup_active for set-membership; this is order.
ACQUISITION_LOG_K = 60
# valid_fraction + K × (item_id_norm, room_index_norm)
ACQUISITION_LOG_DIM = 1 + ACQUISITION_LOG_K * 2

_NAME_TO_ITEM_ID = {name: iid for iid, name in ITEM_IDS.items()}


@dataclass
class RoomTransitionDeque:
    """Last K room *entries* (re-visits included), oldest → newest."""

    capacity: int = ROOM_DEQUE_K
    entries: deque[tuple[str, int]] = field(default_factory=deque)

    def reset(self, room_id: str, *, step: int = 0) -> None:
        self.entries.clear()
        self.record(str(room_id), step=step)

    def record(self, room_id: str, *, step: int) -> None:
        room_id = str(room_id)
        if self.entries and self.entries[-1][0] == room_id:
            return
        self.entries.append((room_id, int(step)))
        while len(self.entries) > self.capacity:
            self.entries.popleft()

    def maybe_record_transition(
        self, room_id: str, *, prev_room: str | None, step: int
    ) -> None:
        room_id = str(room_id)
        if prev_room is not None and str(prev_room) == room_id:
            return
        if self.entries and self.entries[-1][0] == room_id:
            return
        self.record(room_id, step=step)

    def encode(
        self,
        *,
        current_step: int,
        room_index: dict[str, int],
        max_episode_steps: int,
    ) -> np.ndarray:
        v = np.zeros(ROOM_HISTORY_DIM, dtype=np.float32)
        k = int(self.capacity)
        v[0] = min(len(self.entries), k) / float(k)
        tail = list(self.entries)[-k:]
        norm_steps = max(int(max_episode_steps), 1)
        for i, (room_id, enter_step) in enumerate(tail):
            base = 1 + i * 2
            idx = room_index.get(str(room_id), 127)
            v[base] = float(idx) / 128.0
            age = max(int(current_step) - int(enter_step), 0)
            v[base + 1] = min(age / float(norm_steps), 1.0)
        return v


@dataclass
class AcquisitionLog:
    """Last K key-item pickups (any inventory item), oldest → newest."""

    capacity: int = ACQUISITION_LOG_K
    entries: deque[tuple[int, str]] = field(default_factory=deque)

    def reset(self) -> None:
        self.entries.clear()

    def record_pickups(
        self,
        new_item_names: list[str] | set[str],
        *,
        room_id: str,
    ) -> None:
        room_id = str(room_id)
        for name in new_item_names:
            item_id = _NAME_TO_ITEM_ID.get(str(name), 0)
            if item_id <= 0:
                continue
            self.entries.append((int(item_id), room_id))
            while len(self.entries) > self.capacity:
                self.entries.popleft()

    def encode(self, *, room_index: dict[str, int]) -> np.ndarray:
        v = np.zeros(ACQUISITION_LOG_DIM, dtype=np.float32)
        k = int(self.capacity)
        v[0] = min(len(self.entries), k) / float(k)
        tail = list(self.entries)[-k:]
        for i, (item_id, room_id) in enumerate(tail):
            base = 1 + i * 2
            v[base] = float(item_id) / float(MAX_ITEM_ID)
            idx = room_index.get(str(room_id), 127)
            v[base + 1] = float(idx) / 128.0
        return v


@dataclass
class EpisodeHistory:
    room_deque: RoomTransitionDeque = field(default_factory=RoomTransitionDeque)
    acquisitions: AcquisitionLog = field(default_factory=AcquisitionLog)

    def reset(self, room_id: str, *, step: int = 0) -> None:
        self.room_deque.reset(room_id, step=step)
        self.acquisitions.reset()

    def on_step(
        self,
        state: dict[str, Any],
        *,
        prev_state: dict[str, Any] | None,
        new_items: list[str] | set[str],
    ) -> None:
        step = int(state.get("step", 0))
        room_id = str(state.get("room_id", ""))
        prev_room = str(prev_state.get("room_id", "")) if prev_state else None
        self.room_deque.maybe_record_transition(
            room_id, prev_room=prev_room, step=step,
        )
        if new_items:
            self.acquisitions.record_pickups(new_items, room_id=room_id)

    def encode(
        self,
        *,
        current_step: int,
        room_index: dict[str, int],
        max_episode_steps: int,
    ) -> dict[str, np.ndarray]:
        return {
            "history": self.room_deque.encode(
                current_step=current_step,
                room_index=room_index,
                max_episode_steps=max_episode_steps,
            ),
            "acquisitions": self.acquisitions.encode(room_index=room_index),
        }
