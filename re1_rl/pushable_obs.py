"""Egocentric pushable-object slots (up to 2) for the policy.

Each slot packs the live object position (Jill→object compass) and the
remaining object→target vector used by the ±0.5 shove crumb. Slot 0 / 1
are room-205 east / west statues when present; dining 2F uses slot 0 only.
Outside those puzzles the vector is zeros.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from re1_rl.armor_room_puzzle import (
    ARMOR_EAST_SCRIPT_TARGET,
    ARMOR_ROOM_ID,
    ARMOR_WEST_SCRIPT_TARGET,
    _named_statue_xz,
    _step_from_queue,
    armor_stable_statues_seated,
    armor_statue_active,
    armor_vent_index,
)
from re1_rl.dining_statue_puzzle import (
    DINING_STATUE_ROOM_ID,
    _live_statue_xz,
    dining_statue_knocked_from_state,
    dining_statue_nav_target,
    statue_202_active,
)
from re1_rl.obs_encoder import DIST_NORM, FACING_FULL_CIRCLE

PUSHABLE_SLOTS = 2
# present, jill→obj (dx,dz,dist,sin,cos), obj→target remaining (dx,dz,dist), active, seated
PUSHABLE_SLOT_DIM = 11
PUSHABLES_DIM = PUSHABLE_SLOTS * PUSHABLE_SLOT_DIM
PUSHABLES_OBS_KEY = "pushables"


def _compass(state: dict[str, Any], tx: float, tz: float) -> tuple[float, ...]:
    dx = float(tx) - float(state.get("x", 0) or 0)
    dz = float(tz) - float(state.get("z", 0) or 0)
    distance = math.hypot(dx, dz)
    facing = 2.0 * math.pi * float(state.get("facing", 0) or 0) / FACING_FULL_CIRCLE
    relative = math.atan2(dz, dx) - facing
    return (
        float(np.clip(dx / DIST_NORM, -2.0, 2.0)),
        float(np.clip(dz / DIST_NORM, -2.0, 2.0)),
        float(min(distance / DIST_NORM, 2.0)),
        float(math.sin(relative)),
        float(math.cos(relative)),
    )


def _remaining(obj: tuple[float, float], target: tuple[float, float]) -> tuple[float, float, float]:
    dx = float(target[0]) - float(obj[0])
    dz = float(target[1]) - float(obj[1])
    return (
        float(np.clip(dx / DIST_NORM, -2.0, 2.0)),
        float(np.clip(dz / DIST_NORM, -2.0, 2.0)),
        float(min(math.hypot(dx, dz) / DIST_NORM, 2.0)),
    )


def _write_slot(
    v: np.ndarray,
    slot: int,
    *,
    state: dict[str, Any],
    obj: tuple[float, float],
    target: tuple[float, float],
    active: bool,
    seated: bool,
) -> None:
    off = int(slot) * PUSHABLE_SLOT_DIM
    v[off] = 1.0
    v[off + 1 : off + 6] = _compass(state, obj[0], obj[1])
    v[off + 6 : off + 9] = _remaining(obj, target)
    v[off + 9] = 1.0 if active else 0.0
    v[off + 10] = 1.0 if seated else 0.0


def encode_pushables(
    state: dict[str, Any] | None,
    *,
    queue: Any = None,
    planner: Any = None,
) -> np.ndarray:
    """Return ``PUSHABLES_DIM`` floats; zeros when no detectable pushable is live."""
    v = np.zeros(PUSHABLES_DIM, dtype=np.float32)
    if not state:
        return v
    room = str(state.get("room_id", "") or "")

    if room == ARMOR_ROOM_ID and armor_statue_active(queue, state):
        east_seated, west_seated = armor_stable_statues_seated(state)
        active_idx = armor_vent_index(_step_from_queue(queue))
        for slot, prefix, target, seated in (
            (0, "east", ARMOR_EAST_SCRIPT_TARGET, east_seated),
            (1, "west", ARMOR_WEST_SCRIPT_TARGET, west_seated),
        ):
            obj = _named_statue_xz(state, prefix)
            if obj is None:
                continue
            _write_slot(
                v,
                slot,
                state=state,
                obj=obj,
                target=(float(target[0]), float(target[1])),
                active=active_idx == slot,
                seated=seated,
            )
        return v

    if room == DINING_STATUE_ROOM_ID and statue_202_active(planner, state):
        obj = _live_statue_xz(state)
        if obj is None:
            return v
        target = dining_statue_nav_target(state)
        _write_slot(
            v,
            0,
            state=state,
            obj=obj,
            target=target,
            active=True,
            seated=dining_statue_knocked_from_state(state),
        )
    return v
