"""Dining Room 2F (202) statue push → blue jewel progress.

Live probe 2026-08-01 (QuickSave1 monitor): persistent flag u8@0x800C8702 bit
0x10 clears in QS1 and sets when the balcony statue is knocked down. Active
push uses game_state 0x80800040 + player_anim 0x10 (bar bookcase uses 0x44).

Live statue world XZ (QS9 shove hunt 2026-08-09): s16 @ 0x800DB6E0 / +8.
Drop-line pose (QS0 one-push-from-ledge): statue ~(16488, 3452), Jill facing
3072; one forward shove raises Z and sets the knock bit.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from re1_rl.memory_map import DINING_STATUE_FLAG as _FLAG_ADDR
from re1_rl.memory_map import DINING_STATUE_X as _STATUE_X_ADDR
from re1_rl.memory_map import DINING_STATUE_Z as _STATUE_Z_ADDR

DINING_STATUE_ROOM_ID = "202"
DINING_STATUE_CHECKPOINT_ID = "statue_202"
DINING_STATUE_FLAG = _FLAG_ADDR
DINING_STATUE_FLAG_MASK = 0x10
DINING_STATUE_REWARD = 4.0

# Live movable statue entity (confirmed during continuous balcony shove).
DINING_STATUE_X = _STATUE_X_ADDR  # s16
DINING_STATUE_Z = _STATUE_Z_ADDR  # s16

# Active shove (transient); not the bar-bookcase PUSH_GAME_STATE (0x80800044).
DINING_PUSH_GAME_STATE = 0x80800040
DINING_PUSH_ANIM = 0x10

# Slot 10 / QuickSave0 one-push-from-ledge: guide the statue here, then one
# more shove tips it (compass switches to FINAL_PUSH_XZ inside DROP_RADIUS).
DINING_STATUE_DROP_XZ: tuple[int, int] = (16488, 3452)
# Past the railing in +Z from the drop pose (knock drives Z ~3800+).
DINING_STATUE_FINAL_PUSH_XZ: tuple[int, int] = (16488, 4200)
DINING_STATUE_DROP_RADIUS = 700.0

# Dense distance shaping on statue_202 (imperator 2026-08-09):
# ±STEP per env step, potential telescopes to ~BUDGET over REF world units so a
# full balcony shove lands near +10 instead of +0.5×step_count.
DINING_STATUE_PROGRESS_STEP = 0.5
DINING_STATUE_PROGRESS_BUDGET = 10.0
DINING_STATUE_PROGRESS_REF_DIST = 8000.0

FACING_FULL_CIRCLE = 4096.0
DIST_NORM = 4096.0


def dining_statue_knocked_from_state(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    if "dining_statue_knocked" in state:
        return bool(state.get("dining_statue_knocked"))
    raw = state.get("dining_statue_flag")
    if raw is None:
        raw = state.get("dining_statue_flag_raw", 0)
    return bool(int(raw or 0) & DINING_STATUE_FLAG_MASK)


def dining_statue_pushing(state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "")) != DINING_STATUE_ROOM_ID:
        return False
    gs = int(state.get("game_state", 0) or 0)
    anim = int(state.get("player_anim", 0) or 0)
    return gs == DINING_PUSH_GAME_STATE and anim == DINING_PUSH_ANIM


def encode_dining_statue_goal(state: dict[str, Any] | None) -> float:
    """Scalar goal feature: 1.0 once the statue has been knocked down."""
    return 1.0 if dining_statue_knocked_from_state(state) else 0.0


def _live_statue_xz(state: dict[str, Any]) -> tuple[float, float] | None:
    if "dining_statue_x" not in state or "dining_statue_z" not in state:
        return None
    return (float(state["dining_statue_x"]), float(state["dining_statue_z"]))


def dining_statue_nav_target(state: dict[str, Any]) -> tuple[float, float]:
    """World XZ the policy should move/push toward on statue_202.

    Prefer the drop line until the live statue is already there; then aim past
    the ledge so one more forward shove tips it.
    """
    drop = DINING_STATUE_DROP_XZ
    live = _live_statue_xz(state)
    if live is None:
        return (float(drop[0]), float(drop[1]))
    if math.hypot(live[0] - drop[0], live[1] - drop[1]) <= DINING_STATUE_DROP_RADIUS:
        return (
            float(DINING_STATUE_FINAL_PUSH_XZ[0]),
            float(DINING_STATUE_FINAL_PUSH_XZ[1]),
        )
    return (float(drop[0]), float(drop[1]))


def statue_202_active(planner: Any, state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("room_id", "")) != DINING_STATUE_ROOM_ID:
        return False
    if dining_statue_knocked_from_state(state):
        return False
    step = None
    if planner is not None and hasattr(planner, "current_objective"):
        step = planner.current_objective()
    if not isinstance(step, dict):
        return False
    return str(step.get("checkpoint_id", "")) == DINING_STATUE_CHECKPOINT_ID


def encode_dining_statue_compass(
    state: dict[str, Any],
    planner: Any = None,
) -> np.ndarray | None:
    """Egocentric compass toward the drop line (or final shove).

    Returns ``(dx_n, dz_n, dist_n, sin, cos)`` in the same units as the door
    compass, or ``None`` when this checkpoint is not active.
    """
    if not statue_202_active(planner, state):
        return None
    tx, tz = dining_statue_nav_target(state)
    dx = tx - float(state.get("x", 0))
    dz = tz - float(state.get("z", 0))
    distance = math.hypot(dx, dz)
    facing = 2.0 * math.pi * float(state.get("facing", 0)) / FACING_FULL_CIRCLE
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


def _statue_dist_to_nav_target(state: dict[str, Any]) -> float | None:
    live = _live_statue_xz(state)
    if live is None:
        return None
    tx, tz = dining_statue_nav_target(state)
    return math.hypot(live[0] - tx, live[1] - tz)


def dining_statue_progress_phi(remaining: float) -> float:
    """0 at/above REF_DIST from the nav target; BUDGET at the target."""
    capped = min(max(float(remaining), 0.0), DINING_STATUE_PROGRESS_REF_DIST)
    return DINING_STATUE_PROGRESS_BUDGET * (
        1.0 - capped / DINING_STATUE_PROGRESS_REF_DIST
    )


def dining_statue_progress_reward(
    prev_state: dict[str, Any] | None,
    state: dict[str, Any] | None,
    planner: Any = None,
) -> float:
    """Clipped potential on live statue→nav-target distance.

    Closer → up to ``+PROGRESS_STEP``; farther → down to ``-PROGRESS_STEP``.
    A continuous shove across ~REF_DIST world units sums near ``+BUDGET`` (~10).
    Nav-target switches (drop → final shove) rebaseline with zero pay that step.
    """
    if not prev_state or not state:
        return 0.0
    if not statue_202_active(planner, state):
        return 0.0
    if not statue_202_active(planner, prev_state):
        return 0.0
    d0 = _statue_dist_to_nav_target(prev_state)
    d1 = _statue_dist_to_nav_target(state)
    if d0 is None or d1 is None:
        return 0.0
    if dining_statue_nav_target(prev_state) != dining_statue_nav_target(state):
        return 0.0
    raw = dining_statue_progress_phi(d1) - dining_statue_progress_phi(d0)
    return float(
        np.clip(raw, -DINING_STATUE_PROGRESS_STEP, DINING_STATUE_PROGRESS_STEP)
    )
