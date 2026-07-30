"""Allocentric enemy / player world velocity from RAM x/z deltas.

Egocentric ``rel_x``/``rel_z`` confound Jill motion with threat motion.
Slot-keyed world deltas (and Jill's own) let the policy tell stationary
vs walking zombies. Invalidate on room change, teleport jump, or
``not in_control`` (cutscenes / loads).
"""

from __future__ import annotations

import math
from typing import Any

# Per-policy-step scale (frame_skip≈8); clip to [-1, 1] for obs.
VEL_NORM = 1024
# Slot reuse / savestate teleport: larger than a walk step, smaller than room.
DEFAULT_JUMP_THRESHOLD = 8000
# Live enemy table width (encode top 5 elsewhere).
RAM_ENEMY_SLOTS = 6


def clip_vel(v: float, norm: float = VEL_NORM) -> float:
    """Normalize a world delta into [-1, 1]."""
    if norm <= 0:
        return 0.0
    x = float(v) / float(norm)
    if x > 1.0:
        return 1.0
    if x < -1.0:
        return -1.0
    return x


def _world_delta(
    x: int,
    z: int,
    room_id: str,
    in_control: bool,
    prev: tuple[int, int, str] | None,
    *,
    jump_threshold: float,
) -> tuple[int, int, tuple[int, int, str] | None]:
    """Return (vx, vz, next_prev) under invalidate / freeze rules."""
    if not in_control:
        # Freeze prev — no bogus cutscene deltas.
        return 0, 0, prev
    if prev is None:
        return 0, 0, (x, z, room_id)
    prev_x, prev_z, prev_room = prev
    if room_id != prev_room:
        return 0, 0, (x, z, room_id)
    dx = x - prev_x
    dz = z - prev_z
    if math.hypot(dx, dz) > jump_threshold:
        return 0, 0, (x, z, room_id)
    return dx, dz, (x, z, room_id)


class EnemyMotionTracker:
    """Track world_vx/world_vz for all 6 RAM enemy slots."""

    def __init__(self, *, jump_threshold: float = DEFAULT_JUMP_THRESHOLD) -> None:
        self.jump_threshold = float(jump_threshold)
        self.prev: dict[int, tuple[int, int, str]] = {}

    def reset(self) -> None:
        self.prev.clear()

    def update(
        self,
        enemies: list[dict[str, Any]],
        room_id: str,
        in_control: bool,
    ) -> list[dict[str, Any]]:
        """Attach ``world_vx`` / ``world_vz`` to each enemy dict (shallow copy)."""
        room = str(room_id)
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for i, ent in enumerate(enemies or []):
            e = dict(ent)
            slot = int(e.get("slot", i))
            if slot < 0 or slot >= RAM_ENEMY_SLOTS:
                e["world_vx"] = 0
                e["world_vz"] = 0
                out.append(e)
                continue
            seen.add(slot)
            x = int(e.get("x", 0))
            z = int(e.get("z", 0))
            vx, vz, nxt = _world_delta(
                x,
                z,
                room,
                bool(in_control),
                self.prev.get(slot),
                jump_threshold=self.jump_threshold,
            )
            if nxt is None:
                self.prev.pop(slot, None)
            else:
                self.prev[slot] = nxt
            e["world_vx"] = int(vx)
            e["world_vz"] = int(vz)
            out.append(e)
        return out


class PlayerMotionTracker:
    """Jill world velocity — same invalidate rules as enemies."""

    def __init__(self, *, jump_threshold: float = DEFAULT_JUMP_THRESHOLD) -> None:
        self.jump_threshold = float(jump_threshold)
        self.prev: tuple[int, int, str] | None = None

    def reset(self) -> None:
        self.prev = None

    def update(
        self,
        x: int,
        z: int,
        room_id: str,
        in_control: bool,
    ) -> tuple[int, int]:
        vx, vz, nxt = _world_delta(
            int(x),
            int(z),
            str(room_id),
            bool(in_control),
            self.prev,
            jump_threshold=self.jump_threshold,
        )
        self.prev = nxt
        return int(vx), int(vz)
