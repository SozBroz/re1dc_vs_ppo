"""Detect Jill jammed on / pushing a movable object; extend forward holds."""

from __future__ import annotations

from typing import Any

# Live probe 2026-07-10 (QuickSave0 bar bookcase): push engages at ~15 continuous
# Up frames while collision-stalled. Floor extends beyond frame_skip when jammed.
PUSHABLE_HOLD_FRAMES = 30

# game_state while the push animation / slide is active
PUSH_GAME_STATE = 0x80800044

# PLAYER_ANIM_STATE (0x800C51AA)
PUSH_ANIM = 0x10  # actively shoving
JAM_WALK_ANIM = 0x01  # walk cycle pressed into a collider

# Manhattan Δ below this after a forward/run step ⇒ collision stall
FORWARD_STALL_MANHATTAN = 20

FORWARD_ACTION = 1
RUN_FORWARD_ACTION = 5
FORWARD_ACTIONS = frozenset({FORWARD_ACTION, RUN_FORWARD_ACTION})


def touching_pushable(
    state: dict[str, Any] | None,
    *,
    forward_collision_stall: bool = False,
) -> bool:
    """True if Jill is shoving a pushable or jammed into a collider.

    Actively pushing is definitive (``gs`` / anim). Pre-push contact uses the
    walk-into-object anim and/or a prior forward/run step that did not move.
    Wall jams share the pre-push heuristic — extending the hold there is cheap.
    """
    if not state:
        return False
    if int(state.get("game_state", 0)) == PUSH_GAME_STATE:
        return True
    anim = int(state.get("player_anim", 0))
    if anim == PUSH_ANIM or anim == JAM_WALK_ANIM:
        return True
    return bool(forward_collision_stall)


def update_forward_collision_stall(
    prev: dict[str, Any] | None,
    cur: dict[str, Any] | None,
    *,
    action: int,
) -> bool:
    """Update stall flag after a step. Cleared on non-forward actions."""
    if int(action) not in FORWARD_ACTIONS:
        return False
    if not prev or not cur:
        return False
    man = abs(int(cur.get("x", 0)) - int(prev.get("x", 0))) + abs(
        int(cur.get("z", 0)) - int(prev.get("z", 0))
    )
    return man < FORWARD_STALL_MANHATTAN


def forward_hold_frames(
    state: dict[str, Any] | None,
    *,
    action: int,
    frame_skip: int,
    forward_collision_stall: bool = False,
) -> int:
    """Emulated frames for this forward/run step."""
    if int(action) not in FORWARD_ACTIONS:
        return int(frame_skip)
    if touching_pushable(state, forward_collision_stall=forward_collision_stall):
        return max(int(frame_skip), PUSHABLE_HOLD_FRAMES)
    return int(frame_skip)
