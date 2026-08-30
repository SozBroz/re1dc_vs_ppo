"""Detect Jill jammed on / pushing a movable object; extend forward holds."""

from __future__ import annotations

from typing import Any

from re1_rl.dining_statue_puzzle import DINING_PUSH_GAME_STATE, DINING_STATUE_ROOM_ID

# Live probe 2026-07-10 (QuickSave0 bar bookcase): push engages at ~15 continuous
# Up frames while collision-stalled. Floor extends beyond frame_skip when jammed.
PUSHABLE_HOLD_FRAMES = 30

# Armor room 205 (QS2 fresh / live shove 2026-08-30): statues sit 1–2 tiles
# off the side vents. A 30-frame sticky hold (~400+ world units at push
# speed) walks them past the grate into the armor plinth. Keep the hold at
# frame_skip so docking stays steerable.
ARMOR_ROOM_ID = "205"
ARMOR_PUSH_HOLD_FRAMES = 8

# game_state while the push animation / slide is active
PUSH_GAME_STATE = 0x80800044

# PLAYER_ANIM_STATE (0x800C51AA)
PUSH_ANIM = 0x10  # actively shoving
JAM_WALK_ANIM = 0x01  # walk cycle pressed into a collider

# Manhattan Δ below this after a forward/run step ⇒ collision stall
FORWARD_STALL_MANHATTAN = 20

FORWARD_ACTION = 1
RUN_FORWARD_ACTION = 5
TURN_LEFT_ACTION = 3
TURN_RIGHT_ACTION = 4
FORWARD_ACTIONS = frozenset(
    {
        FORWARD_ACTION,
        RUN_FORWARD_ACTION,
    }
)


def touching_pushable(
    state: dict[str, Any] | None,
    *,
    forward_collision_stall: bool = False,
) -> bool:
    """True if Jill is shoving a pushable or jammed into a collider.

    Actively pushing is definitive (``gs`` / anim). Pre-push contact uses the
    walk-into-object anim and/or a prior forward/run step that did not move.
    Wall jams share the pre-push heuristic — extending the hold there is cheap.

    ``PUSH_ANIM`` (0x10) alone is *not* enough: door/settle poses reuse it and
    were extending interact/forward holds into skip+cutscene credit windows.
    """
    if not state:
        return False
    gs = int(state.get("game_state", 0))
    if gs == PUSH_GAME_STATE:
        return True
    room = str(state.get("room_id", "") or "")
    if gs == DINING_PUSH_GAME_STATE and room == DINING_STATUE_ROOM_ID:
        return True
    anim = int(state.get("player_anim", 0))
    if anim == JAM_WALK_ANIM:
        return True
    if anim == PUSH_ANIM:
        return bool(forward_collision_stall)
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
        room = str((state or {}).get("room_id", "") or "")
        hold = (
            ARMOR_PUSH_HOLD_FRAMES
            if room == ARMOR_ROOM_ID
            else PUSHABLE_HOLD_FRAMES
        )
        return max(int(frame_skip), int(hold))
    return int(frame_skip)
