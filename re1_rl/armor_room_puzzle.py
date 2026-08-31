"""Armor Room 205 statue vents → sun crest (pl79 door, pl80 far, pl81 crest).

Live shove 2026-08-30 (QS2): push uses ``gs 0x80800044`` (same as the bar
bookcase). Movable-object XZ shares the player-adjacent work table and is
not a stable pair of addresses like dining 202 (QS1 seated statues go to
sentinel ``-32640``). Progress uses Jill→current-step vent distance while
that push state is active.

Complete a vent when live door-statue XZ ``0x800DB7D8/E0`` matches the QS1
seated pedestal ``(13936, 6347)`` (Jill stands beside it at ``(14083, 6351)``).
The push helper ``0x800DBA44`` leads the pedestal and minted short/long.
Jill on the grate does not mint. Flag ``0x20`` / crest still completes both.

Authored order is door-side first, then far. QS5 seated far pillar is
    ``(5013, 8102)`` with Jill beside it at ``(4827, 8008)`` — not the RDT
    AOT ``(5135, 7236)``. Cabinet crest pickup is (9735, 7236).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from re1_rl.memory_map import ARMOR_PUZZLE_FLAG as _FLAG_ADDR
from re1_rl.memory_map import ARMOR_STATUE_X as _STATUE_X_ADDR
from re1_rl.memory_map import ARMOR_STATUE_Z as _STATUE_Z_ADDR
from re1_rl.pushable import PUSH_GAME_STATE

ARMOR_ROOM_ID = "205"
SUN_CREST_PICKUP_ID = "205:sun_crest:1"
SUN_CREST_BEAT_ID = "sun_crest"
ARMOR_VENT_DOOR_BEAT = "armor_vent_door"
ARMOR_VENT_FAR_BEAT = "armor_vent_far"
ARMOR_VENT_BEATS: tuple[str, str] = (ARMOR_VENT_DOOR_BEAT, ARMOR_VENT_FAR_BEAT)
ARMOR_PUZZLE_FLAG = _FLAG_ADDR
ARMOR_PUZZLE_FLAG_MASK = 0x20

# QS1 seated door statue (Jill beside it at this dock).
ARMOR_VENT_DOOR: tuple[int, int] = (13936, 6347)
ARMOR_VENT_DOOR_DOCK: tuple[int, int] = (14083, 6351)
ARMOR_VENT_FAR: tuple[int, int] = (5013, 8102)
ARMOR_VENT_FAR_DOCK: tuple[int, int] = (4827, 8008)
# Door first, then far — matches authored pl79 → pl80.
ARMOR_VENTS: tuple[tuple[int, int], tuple[int, int]] = (
    ARMOR_VENT_DOOR,
    ARMOR_VENT_FAR,
)
# Pedestal rest (QS2 shove 2026-08-30). Door statue sits ~296 west of its
# grate; a 420 seat radius completed the cell on first contact. Far rest is
# the same offset toward the aisle center.
ARMOR_STATUE_REST: tuple[tuple[int, int], tuple[int, int]] = (
    (13696, 7300),
    (5424, 7300),
)
ARMOR_CABINET_XZ: tuple[int, int] = (9735, 7236)
ARMOR_STATUE_AHEAD_MIN = 80.0
# Live ``0x800DB7D8`` is the nearby pedestal, not a dedicated pair.
# East of this X is the door statue; west is the far statue.
ARMOR_STATUE_SIDE_SPLIT_X = 9000.0
# Door complete waits until Jill reaches the QS1 stand-beside pose.
ARMOR_VENT_DOOR_DOCK_RADIUS = 160.0
ARMOR_VENT_DOOR_SEAT_RADIUS = 170.0
ARMOR_VENT_DOOR_JILL_MAX = 270.0
# Pedestal has left the south rest but has not run on to the RDT AOT.
ARMOR_VENT_DOOR_STATUE_Z_LO = 6200.0
ARMOR_VENT_DOOR_STATUE_Z_HI = 6700.0
# QS5: far pillar on ``(5013, 8102)``, Jill 208 off that grate. Do not
# complete on Jill standing on the empty west grate.
ARMOR_VENT_FAR_SEAT_RADIUS = 100.0
ARMOR_VENT_FAR_JILL_OFF_GRATE = 180.0
ARMOR_VENT_FAR_DOCK_RADIUS = 160.0
ARMOR_STATUE_X = _STATUE_X_ADDR
ARMOR_STATUE_Z = _STATUE_Z_ADDR

ARMOR_STATUE_PROGRESS_STEP = 0.5
ARMOR_STATUE_PROGRESS_BUDGET = 10.0
ARMOR_STATUE_PROGRESS_REF_DIST = 2000.0

FACING_FULL_CIRCLE = 4096.0
DIST_NORM = 4096.0


def _step_from_queue(queue: Any) -> dict[str, Any] | None:
    step = getattr(queue, "current", None) if queue is not None else None
    return step if isinstance(step, dict) else None


def armor_vent_index(step: dict[str, Any] | None) -> int | None:
    """0 = door-side vent, 1 = far vent; else None."""
    if not isinstance(step, dict):
        return None
    for key in ("beat_id", "site_id"):
        label = str(step.get(key) or "")
        if label == ARMOR_VENT_DOOR_BEAT:
            return 0
        if label == ARMOR_VENT_FAR_BEAT:
            return 1
    return None


def armor_vent_step(queue: Any) -> bool:
    return armor_vent_index(_step_from_queue(queue)) is not None


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
    step = _step_from_queue(queue)
    if step is None:
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
    return armor_vent_step(queue) or armor_sun_crest_step(queue)


def _jill_xz(state: dict[str, Any]) -> tuple[float, float]:
    return (float(state.get("x", 0) or 0), float(state.get("z", 0) or 0))


def _dist_to(state: dict[str, Any], target: tuple[float, float]) -> float:
    jx, jz = _jill_xz(state)
    return math.hypot(jx - float(target[0]), jz - float(target[1]))


def armor_statue_xz(state: dict[str, Any] | None) -> tuple[float, float] | None:
    if not state:
        return None
    if "armor_statue_x" not in state or "armor_statue_z" not in state:
        return None
    return (float(state.get("armor_statue_x") or 0), float(state.get("armor_statue_z") or 0))


def armor_vent_step_complete(step: dict[str, Any] | None, state: dict[str, Any] | None) -> bool:
    """True when the authored statue covers that grate, or the puzzle is done.

    Two pedestals share the nearby-object slot. Door uses the east statue
    (x>9000) on the QS1 drain with Jill beside it. Far uses the west statue
    (x<9000) on the QS5 drain; Jill on that grate does not mint.
    """
    idx = armor_vent_index(step)
    if idx is None or not state:
        return False
    if str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    if armor_puzzle_ready_from_state(state) or armor_sun_crest_held(state):
        return True
    if not armor_pushing(state):
        return False
    statue = armor_statue_xz(state)
    if statue is None:
        return False
    jx, jz = _jill_xz(state)
    if math.hypot(jx - statue[0], jz - statue[1]) < ARMOR_STATUE_AHEAD_MIN:
        return False
    if idx == 0:
        if statue[0] < ARMOR_STATUE_SIDE_SPLIT_X:
            return False
        if math.hypot(jx - statue[0], jz - statue[1]) > ARMOR_VENT_DOOR_JILL_MAX:
            return False
        if math.hypot(statue[0] - ARMOR_VENT_DOOR[0], statue[1] - ARMOR_VENT_DOOR[1]) > ARMOR_VENT_DOOR_SEAT_RADIUS:
            return False
        return True
    if statue[0] > ARMOR_STATUE_SIDE_SPLIT_X:
        return False
    if math.hypot(statue[0] - ARMOR_VENT_FAR[0], statue[1] - ARMOR_VENT_FAR[1]) > ARMOR_VENT_FAR_SEAT_RADIUS:
        return False
    if _dist_to(state, ARMOR_VENT_FAR) < ARMOR_VENT_FAR_JILL_OFF_GRATE:
        return False
    if _dist_to(state, ARMOR_VENT_FAR_DOCK) > ARMOR_VENT_FAR_DOCK_RADIUS:
        return False
    return True


def claim_armor_vent_seats(state: dict[str, Any] | None, progress: Any) -> None:
    """Mark a vent seated once the live statue slot covers it (Jill off the grate)."""
    if progress is None or not state:
        return
    seated = armor_vents_seated(state, progress)
    if armor_puzzle_ready_from_state(state) or armor_sun_crest_held(state):
        progress.armor_vents_seated = [True, True]
        return
    statue = armor_statue_xz(state)
    if statue is None:
        progress.armor_vents_seated = seated
        return
    jx, jz = _jill_xz(state)
    if math.hypot(jx - statue[0], jz - statue[1]) < ARMOR_STATUE_AHEAD_MIN:
        progress.armor_vents_seated = seated
        return
    for i, beat in enumerate(ARMOR_VENT_BEATS):
        if seated[i]:
            continue
        if armor_vent_step_complete({"beat_id": beat}, state):
            seated[i] = True
    progress.armor_vents_seated = seated


def armor_statue_nav_target(
    state: dict[str, Any],
    queue: Any = None,
    progress: Any = None,
) -> tuple[float, float] | None:
    """World XZ for the current armor step: door vent, then far, then cabinet."""
    del progress
    if not armor_statue_active(queue, state):
        return None
    idx = armor_vent_index(_step_from_queue(queue))
    if idx is not None:
        if armor_pushing(state):
            vx, vz = ARMOR_VENTS[idx]
            return (float(vx), float(vz))
        rx, rz = ARMOR_STATUE_REST[idx]
        return (float(rx), float(rz))
    if armor_sun_crest_step(queue):
        return (float(ARMOR_CABINET_XZ[0]), float(ARMOR_CABINET_XZ[1]))
    return None


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
    Pays only on the door/far vent steps, not the crest acquire.
    """
    if not prev_state or not state:
        return 0.0
    if not armor_vent_step(queue):
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
    # RE1 facing is clockwise from +X (QS2: 2048 + up = -X). Negate so
    # north/south grate bearings are ahead, not 180° off.
    facing = -2.0 * math.pi * float(state.get("facing", 0) or 0) / FACING_FULL_CIRCLE
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
