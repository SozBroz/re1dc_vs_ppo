"""Crest shed 11B: push metal stepladder under the high shelf, climb, square crank.

Walkthrough (Evil Resource / StrategyWiki): push steps aside so you can get
behind them, shove under the high shelf, climb, take Square Crank.

Live OM XZ (pl197mon2 2026-09-11): ``0x8012F8FC`` / ``+8`` (+ ``0x8012F91C``
mirror). Rest ``(4050, 7500)``; shove deltas match Jill exactly
(``+X`` corridor → ``-Z`` aside → under shelf).

Push hop completes when the live object is under the shelf (mirrors agree),
with crank-held as fallback until that seat is rock-solid in demos.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from re1_rl.item_todo import canonical_item
from re1_rl.memory_map import (
    SHED_STEPLADDER_X,
    SHED_STEPLADDER_X_B,
    SHED_STEPLADDER_Z,
    SHED_STEPLADDER_Z_B,
)

SHED_ROOM_ID = "11B"
SHED_SQUARE_CRANK_PICKUP_ID = "11B:square_crank:1"
SHED_PUSH_STEPLADDER_BEAT = "shed_push_stepladder"
SHED_PUSH_SITE = "shed_stepladder@11B"

# Object geometry from live shove hunt; Jill pads from human hint harness.
HINTS_READY = True

SHED_STEPLADDER_APPROACH_XZ: tuple[int, int] = (4638, 7849)
SHED_STEPLADDER_PUSH_ENDPOINT_XZ: tuple[int, int] = (7500, 8800)
SHED_CRANK_SHELF_XZ: tuple[int, int] = (8750, 9400)

SHED_STEPLADDER_REST_XZ: tuple[int, int] = (4050, 7500)
SHED_STEPLADDER_CORRIDOR_XZ: tuple[int, int] = (6350, 7500)
SHED_STEPLADDER_ASIDE_XZ: tuple[int, int] = (6350, 3650)
# Under high shelf (toward crank interact) — provisional AABB until gold seat.
SHED_STEPLADDER_SEATED_XZ: tuple[int, int] = (8000, 9100)
SHED_STEPLADDER_SEATED_RADIUS = 800.0
SHED_STEPLADDER_MIRROR_TOL = 16.0

SHED_APPROACH_RADIUS = 384.0
SHED_PUSH_GAME_STATE = 0x80800040
SHED_PUSH_ANIM = 0x10

SHED_PROGRESS_STEP = 0.5
SHED_PROGRESS_BUDGET = 10.0
SHED_PROGRESS_REF_DIST = 8000.0
SHED_APPROACH_BUDGET = 0.5
SHED_APPROACH_STEP = 0.1

FACING_FULL_CIRCLE = 4096.0
DIST_NORM = 4096.0

SHED_STEPLADDER_X_ADDR = SHED_STEPLADDER_X
SHED_STEPLADDER_Z_ADDR = SHED_STEPLADDER_Z
SHED_STEPLADDER_X_B_ADDR = SHED_STEPLADDER_X_B
SHED_STEPLADDER_Z_B_ADDR = SHED_STEPLADDER_Z_B


def _jill_xz(state: dict[str, Any]) -> tuple[float, float]:
    return (float(state.get("x", 0) or 0), float(state.get("z", 0) or 0))


def _dist_xy(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _dist(state: dict[str, Any], target: tuple[float, float]) -> float:
    return _dist_xy(_jill_xz(state), target)


def _live_stepladder_xz(state: dict[str, Any] | None) -> tuple[float, float] | None:
    if not state:
        return None
    if "shed_stepladder_x" not in state or "shed_stepladder_z" not in state:
        return None
    return (
        float(state["shed_stepladder_x"]),
        float(state["shed_stepladder_z"]),
    )


def _live_stepladder_xz_b(state: dict[str, Any] | None) -> tuple[float, float] | None:
    if not state:
        return None
    if "shed_stepladder_x_b" not in state or "shed_stepladder_z_b" not in state:
        return None
    return (
        float(state["shed_stepladder_x_b"]),
        float(state["shed_stepladder_z_b"]),
    )


def shed_stepladder_mirrors_agree(state: dict[str, Any] | None) -> bool:
    a = _live_stepladder_xz(state)
    b = _live_stepladder_xz_b(state)
    if a is None or b is None:
        return False
    return _dist_xy(a, b) <= SHED_STEPLADDER_MIRROR_TOL


def _held_names(state: dict[str, Any] | None) -> set[str]:
    if not state:
        return set()
    slots = state.get("inventory_slots") or state.get("inventory") or []
    out: set[str] = set()
    for slot in slots:
        if isinstance(slot, (list, tuple)) and len(slot) >= 1:
            name = canonical_item(slot[0])
            if name and name not in {"", "0", "none"}:
                qty = int(slot[1]) if len(slot) > 1 else 1
                if qty != 0 or name in {
                    "beretta",
                    "shotgun",
                    "bazooka_acid",
                    "bazooka_explosive",
                    "bazooka_flame",
                }:
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


def shed_stepladder_nav_target(state: dict[str, Any] | None) -> tuple[float, float]:
    """Object remaining target: corridor → aside → under-shelf seat."""
    seated = (
        float(SHED_STEPLADDER_SEATED_XZ[0]),
        float(SHED_STEPLADDER_SEATED_XZ[1]),
    )
    live = _live_stepladder_xz(state)
    if live is None:
        return seated
    corridor = (
        float(SHED_STEPLADDER_CORRIDOR_XZ[0]),
        float(SHED_STEPLADDER_CORRIDOR_XZ[1]),
    )
    aside = (
        float(SHED_STEPLADDER_ASIDE_XZ[0]),
        float(SHED_STEPLADDER_ASIDE_XZ[1]),
    )
    if _dist_xy(live, seated) <= SHED_STEPLADDER_SEATED_RADIUS:
        return seated
    # After the south aside shove (or already low Z), aim under the shelf.
    if live[1] <= 5000.0 or _dist_xy(live, aside) <= 700.0:
        return seated
    # After the east corridor shove, aim the aside pocket.
    if live[0] >= 5800.0:
        return aside
    return corridor


def shed_stepladder_push_done(state: dict[str, Any] | None) -> bool:
    """True when live OM XZ is under the shelf and mirrors agree."""
    if not HINTS_READY or not state:
        return False
    live = _live_stepladder_xz(state)
    if live is None:
        return False
    if not shed_stepladder_mirrors_agree(state):
        return False
    return _dist_xy(live, SHED_STEPLADDER_SEATED_XZ) <= SHED_STEPLADDER_SEATED_RADIUS


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
    # Fallback: push+climb+grab during this PL (seat AABB provisional).
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
    """Jill compass: shelf on acquire; else approach → phase endpoint."""
    if not state or str(state.get("room_id", "")) != SHED_ROOM_ID:
        return None
    step = _step_from_queue(queue)
    if isinstance(step, dict) and str(step.get("pickup_id") or "") == SHED_SQUARE_CRANK_PICKUP_ID:
        return float(SHED_CRANK_SHELF_XZ[0]), float(SHED_CRANK_SHELF_XZ[1])
    if not HINTS_READY:
        return None
    approach = SHED_STEPLADDER_APPROACH_XZ
    # Endpoint tracks object phase so compass leads the required aside shove.
    nav = shed_stepladder_nav_target(state)
    endpoint = (int(nav[0]), int(nav[1]))
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


def _obj_dist_to_nav(state: dict[str, Any]) -> float | None:
    live = _live_stepladder_xz(state)
    if live is None:
        return None
    return _dist_xy(live, shed_stepladder_nav_target(state))


def shed_stepladder_progress_phi(remaining: float) -> float:
    ref = max(float(SHED_PROGRESS_REF_DIST), 1.0)
    raw = SHED_PROGRESS_BUDGET * (1.0 - max(float(remaining), 0.0) / ref)
    return float(np.clip(raw, -SHED_PROGRESS_BUDGET, SHED_PROGRESS_BUDGET))


def shed_stepladder_progress_reward(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
    *,
    queue: Any = None,
) -> float:
    if not prev_state or not state:
        return 0.0
    if not shed_stepladder_active(queue, state):
        return 0.0
    if not shed_stepladder_active(queue, prev_state):
        return 0.0
    # Only shape while the push hop is current.
    if not shed_stepladder_step(_step_from_queue(queue)):
        return 0.0
    d0 = _obj_dist_to_nav(prev_state)
    d1 = _obj_dist_to_nav(state)
    if d0 is None or d1 is None:
        return 0.0
    if shed_stepladder_nav_target(prev_state) != shed_stepladder_nav_target(state):
        return 0.0
    if not (shed_pushing(prev_state) or shed_pushing(state)):
        return 0.0
    raw = shed_stepladder_progress_phi(d1) - shed_stepladder_progress_phi(d0)
    return float(np.clip(raw, -SHED_PROGRESS_STEP, SHED_PROGRESS_STEP))


def _jill_dist_to_live_ladder(state: dict[str, Any]) -> float | None:
    live = _live_stepladder_xz(state)
    if live is None:
        return None
    return _dist_xy(_jill_xz(state), live)


def shed_approach_phi(distance: float, reference: float) -> float:
    ref = max(float(reference), 1.0)
    raw = SHED_APPROACH_BUDGET * (1.0 - max(float(distance), 0.0) / ref)
    return float(np.clip(raw, -SHED_APPROACH_BUDGET, SHED_APPROACH_BUDGET))


def shed_approach_progress_reward(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
    *,
    queue: Any = None,
    reference: float | None = None,
) -> float:
    if reference is None or not prev_state or not state:
        return 0.0
    if not shed_stepladder_step(_step_from_queue(queue)):
        return 0.0
    if str(prev_state.get("room_id", "") or "") != SHED_ROOM_ID:
        return 0.0
    if shed_pushing(prev_state) or shed_pushing(state):
        return 0.0
    if shed_stepladder_push_done(state):
        return 0.0
    d0 = _jill_dist_to_live_ladder(prev_state)
    d1 = _jill_dist_to_live_ladder(state)
    if d0 is None or d1 is None:
        return 0.0
    raw = shed_approach_phi(d1, reference) - shed_approach_phi(d0, reference)
    return float(np.clip(raw, -SHED_APPROACH_STEP, SHED_APPROACH_STEP))


def shed_approach_reference(state: dict[str, Any] | None) -> float | None:
    if not state:
        return None
    return _jill_dist_to_live_ladder(state)


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
