"""Pushable contact hold helpers (no emulator)."""

from __future__ import annotations

from re1_rl.pushable import (
    FORWARD_ACTION,
    JAM_WALK_ANIM,
    PUSH_ANIM,
    PUSH_GAME_STATE,
    PUSHABLE_HOLD_FRAMES,
    RUN_FORWARD_ACTION,
    forward_hold_frames,
    touching_pushable,
    update_forward_collision_stall,
)


def test_touching_pushable_by_game_state() -> None:
    assert touching_pushable({"game_state": PUSH_GAME_STATE, "player_anim": 0})
    assert not touching_pushable({"game_state": 0x80800004, "player_anim": 0})


def test_touching_pushable_by_anim() -> None:
    assert touching_pushable({"game_state": 0x80800004, "player_anim": PUSH_ANIM})
    assert touching_pushable({"game_state": 0x80800004, "player_anim": JAM_WALK_ANIM})


def test_touching_pushable_by_stall_flag() -> None:
    assert touching_pushable(
        {"game_state": 0x80800004, "player_anim": 0},
        forward_collision_stall=True,
    )


def test_forward_hold_extends_when_jammed() -> None:
    state = {"game_state": 0x80800004, "player_anim": JAM_WALK_ANIM}
    assert (
        forward_hold_frames(state, action=FORWARD_ACTION, frame_skip=8) == PUSHABLE_HOLD_FRAMES
    )
    assert (
        forward_hold_frames(state, action=RUN_FORWARD_ACTION, frame_skip=8)
        == PUSHABLE_HOLD_FRAMES
    )
    assert forward_hold_frames(state, action=3, frame_skip=8) == 8  # turn_left


def test_forward_hold_normal_when_free() -> None:
    state = {"game_state": 0x80800004, "player_anim": 0, "x": 0, "z": 0}
    assert forward_hold_frames(state, action=FORWARD_ACTION, frame_skip=8) == 8


def test_update_forward_collision_stall() -> None:
    prev = {"x": 100, "z": 200}
    jammed = {"x": 100, "z": 205}
    moved = {"x": 200, "z": 300}
    assert update_forward_collision_stall(prev, jammed, action=FORWARD_ACTION)
    assert not update_forward_collision_stall(prev, moved, action=FORWARD_ACTION)
    assert not update_forward_collision_stall(prev, jammed, action=3)
