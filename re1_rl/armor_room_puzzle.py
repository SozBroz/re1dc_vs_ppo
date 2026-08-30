"""Armor Room 205 statue vents → sun crest (pl78→pl79).

Live shove 2026-08-30 (QS2): push uses ``gs 0x80800044`` (same as the bar
bookcase). Movable-object XZ shares the player-adjacent work table and is
not a stable pair of addresses like dining 202. Progress therefore uses
Jill→vent distance while that push state is active; seating a vent claims
it once when she arrives in-push inside ``SEAT_RADIUS``.

RDT grate AOTs: (5135, 7236) far / (13985, 7236) door-side. Cabinet crest
pickup is (9735, 7236). QS1 vs QS2 sets u8@0x800C8704 bit 0x20 after the
puzzle (crest available / taken).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from re1_rl.memory_map import ARMOR_PUZZLE_FLAG as _FLAG_ADDR
from re1_rl.pushable import PUSH_GAME_STATE

ARMOR_ROOM_ID = "205"
SUN_CREST_PICKUP_ID = "205:sun_crest:1"
SUN_CREST_BEAT_ID = "sun_crest"
ARMOR_PUZZLE_FLAG = _FLAG_ADDR
ARMOR_PUZZLE_FLAG_MASK = 0x20

ARMOR_VENTS: tuple[tuple[int, int], tuple[int, int]] = (
    (5135, 7236),
    (13985, 7236),
)
ARMOR_CABINET_XZ: tuple[int, int] = (9735, 7236)
ARMOR_VENT_SEAT_RADIUS = 420.0

ARMOR_STATUE_PROGRESS_STEP = 0.5
ARMOR_STATUE_PROGRESS_BUDGET = 10.0
ARMOR_STATUE_PROGRESS_REF_DIST = 2000.0

FACING_FULL_CIRCLE = 4096.0
DIST_NORM = 4096.0


def armor_puzzle_ready_from_state(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    if "armor_puzzle_ready" in state:
        return bool(state.get("armor_puzzle_ready"))
    raw = state.get("armor_puzzle_flag")
    return bool(int(raw or 0) & ARMOR_PUZZLE_FLAG_MASK)


def armor_sun_crest_held(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    inv = state.get("inventory") or []
    if any(str(x) == "sun_crest" for x in inv):
        return True
    slots = state.get("inventory_slots") or []
    for entry in slots:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("item")
        elif isinstance(entry, (list, tuple)) and entry:
            name = entry[0]
        else:
            name = None
        if str(name or "") == "sun_crest":
            return True
    return False


def armor_pushing(state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    return int(state.get("game_state", 0) or 0) == PUSH_GAME_STATE


def armor_sun_crest_step(queue: Any) -> bool:
    step = getattr(queue, "current", None) if queue is not None else None
    if not isinstance(step, dict):
        return False
    if str(step.get("beat_id") or "") == SUN_CREST_BEAT_ID:
        return True
    return str(step.get("pickup_id") or "") == SUN_CREST_PICKUP_ID


def armor_vents_seated(state: dict[str, Any] | None, progress: Any = None) -> list[bool]:
    if progress is not None:
        seated = getattr(progress, "armor_vents_seated", None)
        if isinstance(seated, list) and len(seated) >= 2:
            return [bool(seated[0]), bool(seated[1])]
    raw = (state or {}).get("armor_vents_seated")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return [bool(raw[0]), bool(raw[1])]
    if armor_puzzle_ready_from_state(state) or armor_sun_crest_held(state):
        return [True, True]
    return [False, False]


def armor_statue_active(queue: Any, state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    if armor_sun_crest_held(state):
        return False
    return armor_sun_crest_step(queue)


def _jill_xz(state: dict[str, Any]) -> tuple[float, float]:
    return (float(state.get("x", 0) or 0), float(state.get("z", 0) or 0))


def _dist_to(state: dict[str, Any], target: tuple[float, float]) -> float:
    jx, jz = _jill_xz(state)
    return math.hypot(jx - float(target[0]), jz - float(target[1]))


def claim_armor_vent_seats(state: dict[str, Any] | None, progress: Any) -> None:
    """Mark a vent seated once Jill arrives on it while pushing."""
    if progress is None or not state:
        return
    seated = armor_vents_seated(state, progress)
    if not armor_pushing(state):
        progress.armor_vents_seated = seated
        return
    if armor_puzzle_ready_from_state(state):
        progress.armor_vents_seated = [True, True]
        return
    jx, jz = _jill_xz(state)
    for i, (vx, vz) in enumerate(ARMOR_VENTS):
        if seated[i]:
            continue
        if math.hypot(jx - vx, jz - vz) <= ARMOR_VENT_SEAT_RADIUS:
            seated[i] = True
    progress.armor_vents_seated = seated


def armor_statue_nav_target(
    state: dict[str, Any],
    queue: Any = None,
    progress: Any = None,
) -> tuple[float, float] | None:
    """Next world XZ: first open vent nearest Jill, else the cabinet."""
    if not armor_statue_active(queue, state):
        return None
    seated = armor_vents_seated(state, progress)
    if all(seated) or armor_puzzle_ready_from_state(state):
        return (float(ARMOR_CABINET_XZ[0]), float(ARMOR_CABINET_XZ[1]))
    jx, jz = _jill_xz(state)
    open_vents = [
        (float(vx), float(vz))
        for seated_i, (vx, vz) in zip(seated, ARMOR_VENTS)
        if not seated_i
    ]
    if not open_vents:
        return (float(ARMOR_CABINET_XZ[0]), float(ARMOR_CABINET_XZ[1]))
    return min(open_vents, key=lambda t: math.hypot(jx - t[0], jz - t[1]))


def armor_statue_progress_phi(remaining: float) -> float:
    capped = min(max(float(remaining), 0.0), ARMOR_STATUE_PROGRESS_REF_DIST)
    return ARMOR_STATUE_PROGRESS_BUDGET * (
        1.0 - capped / ARMOR_STATUE_PROGRESS_REF_DIST
    )


def armor_statue_progress_reward(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
    queue: Any = None,
    progress: Any = None,
) -> float:
    """Clipped potential on Jill→current vent while pushing in 205.

    Closer → up to ``+PROGRESS_STEP``; farther → down to ``-PROGRESS_STEP``.
    Vent-target switches rebaseline with zero pay. Seating claims stick.
    """
    if not prev_state or not state:
        return 0.0
    if not armor_statue_active(queue, state):
        return 0.0
    if not armor_statue_active(queue, prev_state):
        return 0.0
    if not (armor_pushing(prev_state) or armor_pushing(state)):
        claim_armor_vent_seats(state, progress)
        return 0.0
    t0 = armor_statue_nav_target(prev_state, queue, progress)
    claim_armor_vent_seats(state, progress)
    t1 = armor_statue_nav_target(state, queue, progress)
    if t0 is None or t1 is None:
        return 0.0
    if t0 != t1:
        return 0.0
    raw = armor_statue_progress_phi(_dist_to(state, t1)) - armor_statue_progress_phi(
        _dist_to(prev_state, t0)
    )
    return float(
        np.clip(raw, -ARMOR_STATUE_PROGRESS_STEP, ARMOR_STATUE_PROGRESS_STEP)
    )


def encode_armor_statue_compass(
    state: dict[str, Any],
    queue: Any = None,
    progress: Any = None,
) -> np.ndarray | None:
    target = armor_statue_nav_target(state, queue, progress)
    if target is None:
        return None
    dx = target[0] - float(state.get("x", 0) or 0)
    dz = target[1] - float(state.get("z", 0) or 0)
    distance = math.hypot(dx, dz)
    facing = 2.0 * math.pi * float(state.get("facing", 0) or 0) / FACING_FULL_CIRCLE
    relative = math.atan2(dz, dx) - facing
    return np.asarray(
        [
            float(np.clip(dx / DIST_NORM, -2.0, 2.0)),
            float(np.clip(dz / DIST_NORM, -2.0, 2.0)),
            min(distance / DIST_NORM, 2.0),
            math.sin(relative),
            math.cos(relative),
        ],
        dtype=np.float32,
    )
