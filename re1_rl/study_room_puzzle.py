"""Study Room 20A: insect drain → push fishtank → push cupboard → explosive rounds.

Human gold demo ``pl156_20260910_072945_ok`` (2026-09-10):

1. Insect wall button — interact at ~(2654, 2525); RDT slot 2 @(2800, 2000).
2. Fishtank shove — dining-style ``gs=0x80800040`` + ``anim=0x10``; Jill drives
   south along x≈7359 from z≈7392 → ≈5592 (Δz≈−1550).
3. Cupboard shove — same push gs/anim; Jill drives east along z≈7203 from
   x≈3553 → ≈5103 (Δx≈+1550).
4. Acquire ``20A:explosive_rounds:1`` at ~(3902, 7530).

Drain detection (demo replay RAM hunt 2026-09-10): sticky ``u8@0x800C8701``
bit ``0x20`` rises on the insect interact and stays set through ammo pickup.
Neighbors ``0x800C867A/867C`` are lab/game timers — do not use. ``0x800C8662``
is camera.

Live Om_set XZ for tank/cupboard is still open (player-geometry band at
``0x800DBxxx`` tracks Jill during shoves; armor-style ``0x8012CCxx`` scan was
empty). Compass uses Jill approach/push pads like dining 202 until object
work records are confirmed.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from re1_rl.memory_map import STUDY_TANK_DRAINED_FLAG as _FLAG_ADDR

STUDY_ROOM_ID = "20A"
STUDY_EXPLOSIVE_PICKUP_ID = "20A:explosive_rounds:1"

STUDY_TANK_DRAINED_FLAG = _FLAG_ADDR
STUDY_TANK_DRAINED_MASK = 0x20

# Same shove language as dining balcony (not bar bookcase 0x80800044).
STUDY_PUSH_GAME_STATE = 0x80800040
STUDY_PUSH_ANIM = 0x10

# RDT interactable slot 2 + demo interact pose.
STUDY_INSECT_BUTTON_XZ: tuple[int, int] = (2800, 2000)
STUDY_INSECT_APPROACH_XZ: tuple[int, int] = (2654, 2525)

# Demo shove pads (Jill stand points).
STUDY_TANK_APPROACH_XZ: tuple[int, int] = (7359, 7392)
STUDY_TANK_PUSH_ENDPOINT_XZ: tuple[int, int] = (7359, 5592)
STUDY_CUPBOARD_APPROACH_XZ: tuple[int, int] = (3553, 7203)
STUDY_CUPBOARD_PUSH_ENDPOINT_XZ: tuple[int, int] = (5103, 7203)
STUDY_AMMO_XZ: tuple[int, int] = (3902, 7530)

STUDY_APPROACH_RADIUS = 384.0
# After a finished tank shove Jill sits near the south end of the east wall.
STUDY_TANK_DONE_Z_MAX = 6200.0
STUDY_TANK_DONE_X_MIN = 6800.0
# Cupboard finished when Jill has driven far enough east on the north run.
STUDY_CUPBOARD_DONE_X_MIN = 4800.0
STUDY_CUPBOARD_DONE_Z_MIN = 6800.0
STUDY_CUPBOARD_DONE_Z_MAX = 7600.0

STUDY_INSECT_BEAT = "study_insect_switch"
STUDY_TANK_BEAT = "study_push_fishtank"
STUDY_CUPBOARD_BEAT = "study_push_cupboard"
STUDY_BEATS = (STUDY_INSECT_BEAT, STUDY_TANK_BEAT, STUDY_CUPBOARD_BEAT)

FACING_FULL_CIRCLE = 4096.0
DIST_NORM = 4096.0


def study_tank_drained_from_state(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    if "study_tank_drained" in state:
        return bool(state.get("study_tank_drained"))
    raw = state.get("study_tank_drained_flag")
    if raw is None:
        raw = state.get("study_tank_drained_flag_raw", 0)
    return bool(int(raw or 0) & STUDY_TANK_DRAINED_MASK)


def study_pushing(state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "")) != STUDY_ROOM_ID:
        return False
    gs = int(state.get("game_state", 0) or 0)
    anim = int(state.get("player_anim", 0) or 0)
    return gs == STUDY_PUSH_GAME_STATE and anim == STUDY_PUSH_ANIM


def _jill_xz(state: dict[str, Any]) -> tuple[float, float]:
    return (float(state.get("x", 0) or 0), float(state.get("z", 0) or 0))


def _dist(state: dict[str, Any], target: tuple[float, float]) -> float:
    jx, jz = _jill_xz(state)
    return math.hypot(jx - float(target[0]), jz - float(target[1]))


def study_tank_push_done(state: dict[str, Any] | None) -> bool:
    """Geometry proxy: tank finished south on the east wall.

    Do not treat a north-wall cupboard approach as tank-complete.
    """
    if not state or not study_tank_drained_from_state(state):
        return False
    jx, jz = _jill_xz(state)
    return jx >= STUDY_TANK_DONE_X_MIN and jz <= STUDY_TANK_DONE_Z_MAX


def study_on_cupboard_run(state: dict[str, Any] | None) -> bool:
    """North-wall cupboard lane west of the east-wall tank (not the tank pad)."""
    if not state:
        return False
    jx, jz = _jill_xz(state)
    return (
        float(STUDY_CUPBOARD_APPROACH_XZ[0]) - 400.0
        <= jx
        <= STUDY_CUPBOARD_DONE_X_MIN + 200.0
        and STUDY_CUPBOARD_DONE_Z_MIN <= jz <= STUDY_CUPBOARD_DONE_Z_MAX
    )


def study_cupboard_push_done(state: dict[str, Any] | None) -> bool:
    """Cupboard shoved far enough east on the north run (after drain).

    Exclude the east-wall tank lane (high X) — that pose is tank work, not
    cupboard-complete.
    """
    if not state or not study_tank_drained_from_state(state):
        return False
    jx, jz = _jill_xz(state)
    return (
        STUDY_CUPBOARD_DONE_X_MIN <= jx < STUDY_TANK_DONE_X_MIN
        and STUDY_CUPBOARD_DONE_Z_MIN <= jz <= STUDY_CUPBOARD_DONE_Z_MAX
    )


def _pad_phase(
    state: dict[str, Any],
    approach: tuple[int, int],
    push_endpoint: tuple[int, int],
) -> tuple[float, float]:
    if study_pushing(state) or _dist(state, approach) <= STUDY_APPROACH_RADIUS:
        return float(push_endpoint[0]), float(push_endpoint[1])
    return float(approach[0]), float(approach[1])


def _step_from_queue(queue: Any) -> dict[str, Any] | None:
    if queue is None:
        return None
    cur = getattr(queue, "current", None)
    return cur if isinstance(cur, dict) else None


def study_phase(state: dict[str, Any] | None, *, queue: Any = None) -> str:
    """Return ``insect`` | ``tank`` | ``cupboard`` | ``ammo``."""
    if not state or str(state.get("room_id", "")) != STUDY_ROOM_ID:
        return "insect"
    step = _step_from_queue(queue)
    beat = str((step or {}).get("beat_id") or "")
    if beat == STUDY_INSECT_BEAT:
        return "insect"
    if beat == STUDY_TANK_BEAT:
        return "tank"
    if beat == STUDY_CUPBOARD_BEAT:
        return "cupboard"
    pickup = str((step or {}).get("pickup_id") or "")
    if pickup == STUDY_EXPLOSIVE_PICKUP_ID or str((step or {}).get("op") or "") == "acquire":
        if not study_tank_drained_from_state(state):
            return "insect"
        if study_cupboard_push_done(state):
            return "ammo"
        if study_tank_push_done(state) or study_on_cupboard_run(state):
            return "cupboard"
        return "tank"
    if not study_tank_drained_from_state(state):
        return "insect"
    if study_cupboard_push_done(state):
        return "ammo"
    if study_tank_push_done(state) or study_on_cupboard_run(state):
        return "cupboard"
    return "tank"


def study_room_goal_target(
    state: dict[str, Any] | None, *, queue: Any = None
) -> tuple[float, float] | None:
    """Jill compass target for the active study-room phase."""
    if not state or str(state.get("room_id", "")) != STUDY_ROOM_ID:
        return None
    phase = study_phase(state, queue=queue)
    if phase == "insect":
        return float(STUDY_INSECT_APPROACH_XZ[0]), float(STUDY_INSECT_APPROACH_XZ[1])
    if phase == "tank":
        return _pad_phase(state, STUDY_TANK_APPROACH_XZ, STUDY_TANK_PUSH_ENDPOINT_XZ)
    if phase == "cupboard":
        return _pad_phase(
            state, STUDY_CUPBOARD_APPROACH_XZ, STUDY_CUPBOARD_PUSH_ENDPOINT_XZ
        )
    return float(STUDY_AMMO_XZ[0]), float(STUDY_AMMO_XZ[1])


def encode_study_room_compass(
    state: dict[str, Any] | None, *, queue: Any = None
) -> np.ndarray | None:
    """5-float compass (dx,dz,dist,sin,cos) or None outside 20A."""
    target = study_room_goal_target(state, queue=queue)
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


def study_room_active(queue: Any, state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "")) != STUDY_ROOM_ID:
        return False
    step = _step_from_queue(queue)
    if not isinstance(step, dict):
        return False
    beat = str(step.get("beat_id") or "")
    if beat in STUDY_BEATS:
        return True
    return str(step.get("pickup_id") or "") == STUDY_EXPLOSIVE_PICKUP_ID


def _study_step_beat(step: dict[str, Any] | None) -> str:
    if not isinstance(step, dict):
        return ""
    return str(step.get("beat_id") or step.get("site_id") or "")


def study_room_step_complete(
    step: dict[str, Any] | None, state: dict[str, Any] | None
) -> bool:
    """True when the current 20A study do_puzzle beat is satisfied."""
    if not state or str(state.get("room_id", "")) != STUDY_ROOM_ID:
        return False
    if not isinstance(step, dict) or str(step.get("op") or "") != "do_puzzle":
        return False
    beat = _study_step_beat(step)
    if beat == STUDY_INSECT_BEAT:
        return study_tank_drained_from_state(state)
    if beat == STUDY_TANK_BEAT:
        # Tank shove done only after drain (proxy: Jill south on east wall).
        return study_tank_drained_from_state(state) and study_tank_push_done(state)
    if beat == STUDY_CUPBOARD_BEAT:
        return study_cupboard_push_done(state)
    return False
