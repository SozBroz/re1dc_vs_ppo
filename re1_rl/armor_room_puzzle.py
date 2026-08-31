"""Armor Room 205 statue vents → sun crest.

pl79 requires the exact east-vent placement; pl80 requires both exact vent
placements; pl81 acquires the crest. Placement uses ROOM2050's fixed object-1
and object-2 work records, verified against QS1-9/0, both false mints, live
50-unit shoves, overshoots, and savestate reloads. ``0x800DB7D8/E0`` is player
model geometry and must never grade statue placement.
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

ARMOR_VENT_DOOR: tuple[int, int] = (14035, 7340)
ARMOR_VENT_DOOR_DOCK: tuple[int, int] = (14008, 6518)
ARMOR_VENT_FAR: tuple[int, int] = (4895, 7186)
ARMOR_VENT_FAR_DOCK: tuple[int, int] = (5717, 7136)
# Door first, then far — matches authored pl79 → pl80.
ARMOR_VENTS: tuple[tuple[int, int], tuple[int, int]] = (
    ARMOR_VENT_DOOR,
    ARMOR_VENT_FAR,
)
# ROOM2050 Om_set starting coordinates for object 2 (east) and object 1 (west).
ARMOR_STATUE_REST: tuple[tuple[int, int], tuple[int, int]] = (
    (14035, 6190),
    (8795, 7886),
)
ARMOR_CABINET_XZ: tuple[int, int] = (9735, 7236)
ARMOR_BUTTON_XZ: tuple[int, int] = (9735, 7236)

# Human demonstrations, 2026-08-31. The approach pose puts Jill on the correct
# side; the push endpoint is where a continuous forward hold reached the target.
ARMOR_EAST_APPROACH_XZ: tuple[int, int] = (13947, 5368)  # QS2
ARMOR_EAST_PUSH_ENDPOINT_XZ: tuple[int, int] = (14008, 6518)
ARMOR_WEST_APPROACH_XZ: tuple[int, int] = (8704, 8708)  # QS6, align Z first
ARMOR_WEST_PUSH_ENDPOINT_XZ: tuple[int, int] = (8539, 8008)
ARMOR_WEST_LATERAL_APPROACH_XZ: tuple[int, int] = (9617, 7179)
ARMOR_WEST_LATERAL_PUSH_ENDPOINT_XZ: tuple[int, int] = (5717, 7136)

# Exact ROOM2050 object-work coordinates at the demonstrated QS0/QS9 seats.
ARMOR_EAST_SCRIPT_TARGET: tuple[int, int] = ARMOR_VENT_DOOR
ARMOR_WEST_SCRIPT_TARGET: tuple[int, int] = ARMOR_VENT_FAR
ARMOR_SCRIPT_TARGET_TOLERANCE = 8
ARMOR_APPROACH_RADIUS = 384.0
ARMOR_STATUE_AHEAD_MIN = 80.0
# Live ``0x800DB7D8`` is the nearby pedestal, not a dedicated pair.
# East of this X is the door statue; west is the far statue.
ARMOR_STATUE_SIDE_SPLIT_X = 9000.0
# Same rule both vents: statue on that grate, Jill beside it (not on it).
ARMOR_VENT_SEAT_RADIUS = 220.0
ARMOR_VENT_JILL_MAX = 320.0
# Kept names so older tests / probes keep importing.
ARMOR_VENT_DOOR_SEAT_RADIUS = ARMOR_VENT_SEAT_RADIUS
ARMOR_VENT_DOOR_JILL_MAX = ARMOR_VENT_JILL_MAX
ARMOR_VENT_FAR_SEAT_RADIUS = ARMOR_VENT_SEAT_RADIUS
ARMOR_STATUE_X = _STATUE_X_ADDR
ARMOR_STATUE_Z = _STATUE_Z_ADDR

ARMOR_STATUE_PROGRESS_STEP = 0.5
ARMOR_STATUE_PROGRESS_BUDGET = 4.0

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


def _stable_statue_at_target(
    state: dict[str, Any],
    prefix: str,
    target: tuple[int, int],
) -> bool:
    pairs = [
        (f"armor_{prefix}_statue_x", f"armor_{prefix}_statue_z"),
        (f"armor_{prefix}_statue_x_b", f"armor_{prefix}_statue_z_b"),
        (f"armor_{prefix}_statue_x_c", f"armor_{prefix}_statue_z_c"),
    ]
    if any(x_key not in state or z_key not in state for x_key, z_key in pairs):
        return False
    return all(
        abs(int(state.get(x_key, 0) or 0) - target[0])
        <= ARMOR_SCRIPT_TARGET_TOLERANCE
        and abs(int(state.get(z_key, 0) or 0) - target[1])
        <= ARMOR_SCRIPT_TARGET_TOLERANCE
        for x_key, z_key in pairs
    )


def armor_stable_statues_seated(state: dict[str, Any] | None) -> tuple[bool, bool]:
    """Placement truth from stable room-script fields: (east, west)."""
    if not state or str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False, False
    return (
        _stable_statue_at_target(state, "east", ARMOR_EAST_SCRIPT_TARGET),
        _stable_statue_at_target(state, "west", ARMOR_WEST_SCRIPT_TARGET),
    )


def armor_statue_goal_target(
    state: dict[str, Any] | None,
) -> tuple[float, float] | None:
    """Phase-aware compass target; supplies guidance only and never pays reward."""
    if not state or str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return None
    if armor_puzzle_ready_from_state(state) or armor_sun_crest_held(state):
        return None

    east_seated, west_seated = armor_stable_statues_seated(state)
    if not east_seated:
        target = (
            ARMOR_EAST_PUSH_ENDPOINT_XZ
            if armor_pushing(state)
            or _dist_to(state, ARMOR_EAST_APPROACH_XZ) <= ARMOR_APPROACH_RADIUS
            else ARMOR_EAST_APPROACH_XZ
        )
        return float(target[0]), float(target[1])
    if not west_seated:
        west = _named_statue_xz(state, "west")
        depth_aligned = (
            west is not None
            and abs(west[1] - ARMOR_WEST_SCRIPT_TARGET[1])
            <= ARMOR_SCRIPT_TARGET_TOLERANCE
        )
        if depth_aligned:
            target = (
                ARMOR_WEST_LATERAL_PUSH_ENDPOINT_XZ
                if armor_pushing(state)
                or _dist_to(state, ARMOR_WEST_LATERAL_APPROACH_XZ)
                <= ARMOR_APPROACH_RADIUS
                else ARMOR_WEST_LATERAL_APPROACH_XZ
            )
        else:
            target = (
                ARMOR_WEST_PUSH_ENDPOINT_XZ
                if armor_pushing(state)
                or _dist_to(state, ARMOR_WEST_APPROACH_XZ)
                <= ARMOR_APPROACH_RADIUS
                else ARMOR_WEST_APPROACH_XZ
            )
        return float(target[0]), float(target[1])
    return float(ARMOR_BUTTON_XZ[0]), float(ARMOR_BUTTON_XZ[1])


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


def _named_statue_xz(
    state: dict[str, Any] | None, prefix: str
) -> tuple[float, float] | None:
    if not state:
        return None
    x_key = f"armor_{prefix}_statue_x"
    z_key = f"armor_{prefix}_statue_z"
    if x_key not in state or z_key not in state:
        return None
    return float(state.get(x_key) or 0), float(state.get(z_key) or 0)


def armor_vent_step_complete(step: dict[str, Any] | None, state: dict[str, Any] | None) -> bool:
    """Strict helper-cell gate: pl79=east; pl80=east AND west."""
    idx = armor_vent_index(step)
    if idx is None or not state:
        return False
    if str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    east_seated, west_seated = armor_stable_statues_seated(state)
    return east_seated if idx == 0 else east_seated and west_seated


def armor_vent_physically_seated(
    step: dict[str, Any] | None, state: dict[str, Any] | None
) -> bool:
    """Stable room-script placement gate used by authoring and live minting."""
    return armor_vent_step_complete(step, state)


def claim_armor_vent_seats(state: dict[str, Any] | None, progress: Any) -> None:
    """Mirror stable placement truth into episode telemetry."""
    if progress is None or not state:
        return
    progress.armor_vents_seated = list(armor_stable_statues_seated(state))


def armor_statue_nav_target(
    state: dict[str, Any],
    queue: Any = None,
    progress: Any = None,
) -> tuple[float, float] | None:
    """World XZ for the phase-aware approach/push/button compass."""
    del progress
    if not armor_statue_active(queue, state):
        return None
    return armor_statue_goal_target(state)


def armor_statue_progress_phi(remaining: float, reference: float) -> float:
    distance = max(float(remaining), 0.0)
    return ARMOR_STATUE_PROGRESS_BUDGET * (
        1.0 - distance / max(float(reference), 1.0)
    )


def armor_statue_progress_reward(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
    queue: Any = None,
    progress: Any = None,
) -> float:
    """Shove-only object potential: toward the exact seat pays; away is punished."""
    del progress
    if not prev_state or not state:
        return 0.0
    idx = armor_vent_index(_step_from_queue(queue))
    if idx is None:
        return 0.0
    if str(prev_state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return 0.0
    if str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return 0.0
    if not (armor_pushing(prev_state) or armor_pushing(state)):
        return 0.0

    if idx == 0:
        prefix = "east"
        rest = ARMOR_STATUE_REST[0]
        target = ARMOR_EAST_SCRIPT_TARGET
    else:
        prefix = "west"
        rest = ARMOR_STATUE_REST[1]
        target = ARMOR_WEST_SCRIPT_TARGET

    prev_xz = _named_statue_xz(prev_state, prefix)
    current_xz = _named_statue_xz(state, prefix)
    if prev_xz is None or current_xz is None:
        return 0.0
    reference = math.hypot(float(rest[0] - target[0]), float(rest[1] - target[1]))
    raw = armor_statue_progress_phi(
        math.hypot(current_xz[0] - target[0], current_xz[1] - target[1]),
        reference,
    ) - armor_statue_progress_phi(
        math.hypot(prev_xz[0] - target[0], prev_xz[1] - target[1]),
        reference,
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
