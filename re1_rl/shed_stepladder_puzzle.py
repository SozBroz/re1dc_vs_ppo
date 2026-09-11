"""Crest shed 11B: push metal stepladder under the high shelf, climb, square crank.

Walkthrough (Evil Resource / StrategyWiki): push steps aside so you can get
behind them, shove under the high shelf, climb, take Square Crank.

Operator will fill approach / endpoint pads when ready (``HINTS_READY``).
Until then completion latches on ``square_crank`` held in 11B so the push PL
can mint after a successful push+climb+grab; the following acquire hop
accepts already-held crank in-room.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from re1_rl.item_todo import canonical_item

SHED_ROOM_ID = "11B"
SHED_SQUARE_CRANK_PICKUP_ID = "11B:square_crank:1"
SHED_PUSH_STEPLADDER_BEAT = "shed_push_stepladder"
SHED_PUSH_SITE = "shed_stepladder@11B"

# Flip when approach / endpoint pads are operator-confirmed.
HINTS_READY = False

# Placeholders — fill from gold demo / live probe, then set HINTS_READY.
SHED_STEPLADDER_APPROACH_XZ: tuple[int, int] | None = None
SHED_STEPLADDER_PUSH_ENDPOINT_XZ: tuple[int, int] | None = None
# Approx shelf interact (RDT message slot near high shelf) — climb/acquire pad.
SHED_CRANK_SHELF_XZ: tuple[int, int] = (8750, 9400)

SHED_APPROACH_RADIUS = 384.0
# Dining / study shove language (bar bookcase uses 0x80800044).
SHED_PUSH_GAME_STATE = 0x80800040
SHED_PUSH_ANIM = 0x10

FACING_FULL_CIRCLE = 4096.0
DIST_NORM = 4096.0


def _jill_xz(state: dict[str, Any]) -> tuple[float, float]:
    return (float(state.get("x", 0) or 0), float(state.get("z", 0) or 0))


def _dist(state: dict[str, Any], target: tuple[float, float]) -> float:
    jx, jz = _jill_xz(state)
    return math.hypot(jx - float(target[0]), jz - float(target[1]))


def _held_names(state: dict[str, Any] | None) -> set[str]:
    """Inventory item names currently held (no import of planner_loyal)."""
    if not state:
        return set()
    slots = state.get("inventory_slots") or state.get("inventory") or []
    out: set[str] = set()
    for slot in slots:
        if isinstance(slot, (list, tuple)) and len(slot) >= 1:
            name = canonical_item(slot[0])
            if name and name not in {"", "0", "none"}:
                qty = int(slot[1]) if len(slot) > 1 else 1
                if qty != 0 or name in {"beretta", "shotgun", "bazooka_acid", "bazooka_explosive", "bazooka_flame"}:
                    out.add(name)
        elif isinstance(slot, dict):
            name = canonical_item(slot.get("item") or slot.get("name") or "")
            if name:
                out.add(name)
    return out


def shed_pushing(state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "")) != SHED_ROOM_ID:
        return False
    gs = int(state.get("game_state", 0) or 0)
    anim = int(state.get("player_anim", 0) or 0)
    return gs == SHED_PUSH_GAME_STATE and anim == SHED_PUSH_ANIM


def shed_stepladder_push_done(state: dict[str, Any] | None) -> bool:
    """Geometry proxy once HINTS_READY; else False (use crank latch)."""
    if not HINTS_READY or not state:
        return False
    endpoint = SHED_STEPLADDER_PUSH_ENDPOINT_XZ
    if endpoint is None:
        return False
    return _dist(state, (float(endpoint[0]), float(endpoint[1]))) <= SHED_APPROACH_RADIUS


def _step_beat(step: dict[str, Any] | None) -> str:
    if not isinstance(step, dict):
        return ""
    return str(step.get("beat_id") or step.get("site_id") or "")


def shed_stepladder_step(step: dict[str, Any] | None) -> bool:
    if not isinstance(step, dict) or str(step.get("op") or "") != "do_puzzle":
        return False
    beat = _step_beat(step)
    return beat in {SHED_PUSH_STEPLADDER_BEAT, SHED_PUSH_SITE}


def shed_stepladder_step_complete(
    step: dict[str, Any] | None, state: dict[str, Any] | None
) -> bool:
    """True when the shed stepladder push hop is satisfied."""
    if not shed_stepladder_step(step):
        return False
    if not state or str(state.get("room_id", "")) != SHED_ROOM_ID:
        return False
    if HINTS_READY and shed_stepladder_push_done(state):
        return True
    # Provisional: push+climb+grab during this PL (hints not filled yet).
    return "square_crank" in _held_names(state)


def shed_square_crank_held_in_room(state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "")) != SHED_ROOM_ID:
        return False
    return "square_crank" in _held_names(state)


def _step_from_queue(queue: Any) -> dict[str, Any] | None:
    if queue is None:
        return None
    cur = getattr(queue, "current", None)
    return cur if isinstance(cur, dict) else None


def shed_stepladder_active(queue: Any, state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "")) != SHED_ROOM_ID:
        return False
    step = _step_from_queue(queue)
    if shed_stepladder_step(step):
        return True
    if not isinstance(step, dict):
        return False
    return str(step.get("pickup_id") or "") == SHED_SQUARE_CRANK_PICKUP_ID


def shed_stepladder_goal_target(
    state: dict[str, Any] | None, *, queue: Any = None
) -> tuple[float, float] | None:
    """Jill compass target; None until HINTS_READY (acquire uses shelf pad)."""
    if not state or str(state.get("room_id", "")) != SHED_ROOM_ID:
        return None
    step = _step_from_queue(queue)
    if isinstance(step, dict) and str(step.get("pickup_id") or "") == SHED_SQUARE_CRANK_PICKUP_ID:
        return float(SHED_CRANK_SHELF_XZ[0]), float(SHED_CRANK_SHELF_XZ[1])
    if not HINTS_READY:
        return None
    approach = SHED_STEPLADDER_APPROACH_XZ
    endpoint = SHED_STEPLADDER_PUSH_ENDPOINT_XZ
    if approach is None or endpoint is None:
        return None
    ax, az = float(approach[0]), float(approach[1])
    ex, ez = float(endpoint[0]), float(endpoint[1])
    if shed_pushing(state) or _dist(state, (ax, az)) <= SHED_APPROACH_RADIUS:
        return ex, ez
    return ax, az


def encode_shed_stepladder_compass(
    state: dict[str, Any] | None, *, queue: Any = None
) -> np.ndarray | None:
    target = shed_stepladder_goal_target(state, queue=queue)
    if target is None or not state:
        return None
    dx = float(target[0]) - float(state.get("x", 0) or 0)
    dz = float(target[1]) - float(state.get("z", 0) or 0)
    distance = math.hypot(dx, dz)
    facing = -2.0 * math.pi * float(state.get("facing", 0) or 0) / FACING_FULL_CIRCLE
    relative = math.atan2(dz, dx) - facing
    return np.asarray(
        [
            float(np.clip(dx / DIST_NORM, -2.0, 2.0)),
            float(np.clip(dz / DIST_NORM, -2.0, 2.0)),
            float(min(distance / DIST_NORM, 2.0)),
            float(math.sin(relative)),
            float(math.cos(relative)),
        ],
        dtype=np.float32,
    )


def shed_acquire_crank_already_held(
    step: dict[str, Any] | None, state: dict[str, Any] | None
) -> bool:
    """True when square_crank acquire can mint because crank is already held."""
    if not isinstance(step, dict) or str(step.get("op") or "") != "acquire":
        return False
    want = str(step.get("pickup_id") or "")
    if want != SHED_SQUARE_CRANK_PICKUP_ID and canonical_item(
        want.split(":")[1] if ":" in want else want
    ) != "square_crank":
        return False
    return shed_square_crank_held_in_room(state)
