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

# ROOM2050 object-work coordinates at the demonstrated QS0/QS9 seats.
# East (pl79) stays pixel-tight. West mint gate is a human-validated AABB
# (2026-09-01): X 4845..5195, Z 7086..7336 — covers QS (4895, 7186),
# button+no-gas (4945, 7136), and live seat (4895, 7336).
ARMOR_EAST_SCRIPT_TARGET: tuple[int, int] = ARMOR_VENT_DOOR
ARMOR_WEST_SCRIPT_TARGET: tuple[int, int] = ARMOR_VENT_FAR
ARMOR_EAST_SCRIPT_TARGET_TOLERANCE = 8
ARMOR_WEST_SEAT_X_MIN = 4845
ARMOR_WEST_SEAT_X_MAX = 5195
ARMOR_WEST_SEAT_Z_MIN = 7086
ARMOR_WEST_SEAT_Z_MAX = 7336
# Loosest half-width from the QS target; keep for depth-align / older callers.
ARMOR_WEST_SCRIPT_TARGET_TOLERANCE = max(
    ARMOR_WEST_SCRIPT_TARGET[0] - ARMOR_WEST_SEAT_X_MIN,
    ARMOR_WEST_SEAT_X_MAX - ARMOR_WEST_SCRIPT_TARGET[0],
    ARMOR_WEST_SCRIPT_TARGET[1] - ARMOR_WEST_SEAT_Z_MIN,
    ARMOR_WEST_SEAT_Z_MAX - ARMOR_WEST_SCRIPT_TARGET[1],
)
# Back-compat alias used by west depth-align / older callers.
ARMOR_SCRIPT_TARGET_TOLERANCE = ARMOR_WEST_SCRIPT_TARGET_TOLERANCE
# Torn object records (mirrors disagree) must still fail even inside the AOT.
ARMOR_MIRROR_AGREE_TOLERANCE = 8
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
# Live shoves move 50 world units; ignore mirror jitter below this.
ARMOR_STATUE_MOVE_THRESHOLD = 25
ARMOR_INPLACE_STATUE_PUSH_PENALTY = 4.0

# pl79->80 spawn (and any agent-minted pl79) leaves Jill jammed against the
# seated east statue facing it; the first forward-ish action re-shoves it
# (-4 terminal). Imperator 2026-09-01: a +0.5 potential on Jill's distance to
# the west statue (walking away from it is punished symmetrically) pays for
# turning off the east statue and crossing the room.
ARMOR_APPROACH_BUDGET = 0.5
ARMOR_APPROACH_STEP = 0.1
# Any HP loss in 205 is the poison gas (button pressed with a vent open).
# Imperator 2026-09-01: terminal, same magnitude as the other puzzle fails.
ARMOR_GAS_DAMAGE_PENALTY = 4.0

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


def _stable_statue_mirrors(
    state: dict[str, Any],
    prefix: str,
) -> list[tuple[int, int]] | None:
    pairs = [
        (f"armor_{prefix}_statue_x", f"armor_{prefix}_statue_z"),
        (f"armor_{prefix}_statue_x_b", f"armor_{prefix}_statue_z_b"),
        (f"armor_{prefix}_statue_x_c", f"armor_{prefix}_statue_z_c"),
    ]
    if any(x_key not in state or z_key not in state for x_key, z_key in pairs):
        return None
    return [
        (
            int(state.get(x_key, 0) or 0),
            int(state.get(z_key, 0) or 0),
        )
        for x_key, z_key in pairs
    ]


def _mirrors_agree(coords: list[tuple[int, int]]) -> bool:
    xs = [x for x, _ in coords]
    zs = [z for _, z in coords]
    return (
        max(xs) - min(xs) <= ARMOR_MIRROR_AGREE_TOLERANCE
        and max(zs) - min(zs) <= ARMOR_MIRROR_AGREE_TOLERANCE
    )


def _stable_statue_at_target(
    state: dict[str, Any],
    prefix: str,
    target: tuple[int, int],
    *,
    tolerance: int,
) -> bool:
    coords = _stable_statue_mirrors(state, prefix)
    if coords is None:
        return False
    if not all(
        abs(x - target[0]) <= tolerance and abs(z - target[1]) <= tolerance
        for x, z in coords
    ):
        return False
    return _mirrors_agree(coords)


def _stable_statue_in_box(
    state: dict[str, Any],
    prefix: str,
    *,
    x_min: int,
    x_max: int,
    z_min: int,
    z_max: int,
) -> bool:
    coords = _stable_statue_mirrors(state, prefix)
    if coords is None:
        return False
    if not all(x_min <= x <= x_max and z_min <= z <= z_max for x, z in coords):
        return False
    return _mirrors_agree(coords)


def armor_west_depth_aligned(west_xz: tuple[float, float] | None) -> bool:
    """True when west statue Z is inside the human-validated vent seat band."""
    if west_xz is None:
        return False
    return ARMOR_WEST_SEAT_Z_MIN <= float(west_xz[1]) <= ARMOR_WEST_SEAT_Z_MAX


def armor_stable_statues_seated(state: dict[str, Any] | None) -> tuple[bool, bool]:
    """Placement truth from stable ROOM2050 object-work mirrors: (east, west)."""
    if not state or str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False, False
    return (
        _stable_statue_at_target(
            state,
            "east",
            ARMOR_EAST_SCRIPT_TARGET,
            tolerance=ARMOR_EAST_SCRIPT_TARGET_TOLERANCE,
        ),
        _stable_statue_in_box(
            state,
            "west",
            x_min=ARMOR_WEST_SEAT_X_MIN,
            x_max=ARMOR_WEST_SEAT_X_MAX,
            z_min=ARMOR_WEST_SEAT_Z_MIN,
            z_max=ARMOR_WEST_SEAT_Z_MAX,
        ),
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
        depth_aligned = armor_west_depth_aligned(west)
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


def _statue_moved(
    prev_state: dict[str, Any],
    state: dict[str, Any],
    prefix: str,
) -> bool:
    prev_xz = _named_statue_xz(prev_state, prefix)
    current_xz = _named_statue_xz(state, prefix)
    if prev_xz is None or current_xz is None:
        return False
    return (
        math.hypot(current_xz[0] - prev_xz[0], current_xz[1] - prev_xz[1])
        >= ARMOR_STATUE_MOVE_THRESHOLD
    )


def armor_inplace_statue_push_detected(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
    queue: Any = None,
) -> bool:
    """True when a seated vent statue moves during a shove on the pl79->80 step only.

    pl79->80 (``armor_vent_far``): east is already on its vent; pushing it ends
    the episode. pl78->79 is unchanged.
    """
    idx = armor_vent_index(_step_from_queue(queue))
    if idx != 1 or not prev_state or not state:
        return False
    if str(prev_state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    if str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    if not (armor_pushing(prev_state) or armor_pushing(state)):
        return False

    prev_seated = armor_stable_statues_seated(prev_state)
    for prefix, seated in zip(("east", "west"), prev_seated, strict=True):
        if seated and _statue_moved(prev_state, state, prefix):
            return True
    return False


def armor_far_leg_active(
    queue: Any, state: dict[str, Any] | None
) -> bool:
    """True while the current planner step is ``armor_vent_far`` in room 205."""
    if not state or str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    return armor_vent_index(_step_from_queue(queue)) == 1


def armor_approach_phi(distance: float, reference: float) -> float:
    ref = max(float(reference), 1.0)
    raw = ARMOR_APPROACH_BUDGET * (1.0 - max(float(distance), 0.0) / ref)
    return float(np.clip(raw, -ARMOR_APPROACH_BUDGET, ARMOR_APPROACH_BUDGET))


def armor_approach_progress_reward(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
    queue: Any,
    reference: float | None,
) -> float:
    """Potential on Jill's distance to the live west statue (far-vent leg only).

    ``reference`` is the distance when the leg began (baselined by the caller
    on the first far-vent step, so it works from the pl79 reset and after an
    in-episode pl78->79 completion alike, wherever pl79 was minted). The
    potential telescopes to at most +0.5 over the whole approach; retreating
    toward the door pays it back. Zero while a shove is active — the statue
    moves with Jill then.
    """
    if reference is None or not prev_state or not state:
        return 0.0
    if not armor_far_leg_active(queue, state):
        return 0.0
    if str(prev_state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return 0.0
    if armor_pushing(prev_state) or armor_pushing(state):
        return 0.0
    prev_west = _named_statue_xz(prev_state, "west")
    west = _named_statue_xz(state, "west")
    if prev_west is None or west is None:
        return 0.0
    raw = armor_approach_phi(_dist_to(state, west), reference) - armor_approach_phi(
        _dist_to(prev_state, prev_west), reference
    )
    return float(np.clip(raw, -ARMOR_APPROACH_STEP, ARMOR_APPROACH_STEP))


def armor_approach_reference(state: dict[str, Any] | None) -> float | None:
    """Jill-to-west-statue distance used to baseline the approach potential."""
    if not state:
        return None
    west = _named_statue_xz(state, "west")
    if west is None:
        return None
    return float(_dist_to(state, west))


def armor_gas_damage_detected(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> bool:
    """HP dropped while Jill stayed in 205: only the vent gas does that.

    Death on the same step is left to the ordinary death terminal.
    """
    if not prev_state or not state:
        return False
    if str(prev_state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    if str(state.get("room_id", "") or "") != ARMOR_ROOM_ID:
        return False
    if state.get("dead"):
        return False
    prev_hp = int(prev_state.get("hp", 0) or 0)
    hp = int(state.get("hp", 0) or 0)
    return prev_hp > 0 and hp < prev_hp


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
