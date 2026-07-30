"""Allocentric enemy / player world velocity tracker."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.enemy_motion import (
    VEL_NORM,
    EnemyMotionTracker,
    PlayerMotionTracker,
    clip_vel,
)


def _zombie(slot: int, x: int, z: int, **extra):
    e = {"slot": slot, "x": x, "z": z, "hp": 100, "alive": 1, "type_id": 1}
    e.update(extra)
    return e


def test_clip_vel_norm_and_saturate() -> None:
    assert VEL_NORM == 1024
    assert clip_vel(0) == 0.0
    assert clip_vel(512) == pytest.approx(0.5)
    assert clip_vel(-1024) == pytest.approx(-1.0)
    assert clip_vel(5000) == 1.0
    assert clip_vel(-5000) == -1.0


def test_stationary_enemy_while_player_moves() -> None:
    """Enemy world velocity stays ~0 when only Jill walks."""
    et = EnemyMotionTracker()
    pt = PlayerMotionTracker()
    room = "104"
    zx, zz = 12000, 10000
    # Seed both trackers.
    et.update([_zombie(0, zx, zz)], room, True)
    pt.update(10000, 10000, room, True)
    # Jill walks +x; zombie fixed.
    for step in range(1, 6):
        px = 10000 + step * 200
        outs = et.update([_zombie(0, zx, zz)], room, True)
        pvx, pvz = pt.update(px, 10000, room, True)
        assert outs[0]["world_vx"] == 0
        assert outs[0]["world_vz"] == 0
        assert pvx == 200
        assert pvz == 0


def test_walking_zombie_nonzero_world_velocity() -> None:
    et = EnemyMotionTracker()
    room = "104"
    et.update([_zombie(0, 10000, 10000)], room, True)
    outs = et.update([_zombie(0, 10000 + 180, 10000 + 40)], room, True)
    assert outs[0]["world_vx"] == 180
    assert outs[0]["world_vz"] == 40


def test_room_change_zeros_velocity() -> None:
    et = EnemyMotionTracker()
    et.update([_zombie(0, 10000, 10000)], "104", True)
    # Same coords, new room → invalidate.
    outs = et.update([_zombie(0, 10000, 10000)], "105", True)
    assert outs[0]["world_vx"] == 0
    assert outs[0]["world_vz"] == 0
    # Next step in new room can report motion again.
    outs = et.update([_zombie(0, 10100, 10000)], "105", True)
    assert outs[0]["world_vx"] == 100
    assert outs[0]["world_vz"] == 0


def test_position_jump_invalidates() -> None:
    et = EnemyMotionTracker(jump_threshold=8000)
    et.update([_zombie(0, 10000, 10000)], "104", True)
    # Slot reuse / teleport > 8000.
    outs = et.update([_zombie(0, 20000, 10000)], "104", True)
    assert outs[0]["world_vx"] == 0
    assert outs[0]["world_vz"] == 0


def test_not_in_control_zeros_and_freezes_prev() -> None:
    et = EnemyMotionTracker()
    et.update([_zombie(0, 10000, 10000)], "104", True)
    outs = et.update([_zombie(0, 10500, 10000)], "104", False)
    assert outs[0]["world_vx"] == 0
    assert outs[0]["world_vz"] == 0
    # Prev frozen at seed; resume with small move from seed → full delta.
    outs = et.update([_zombie(0, 10150, 10000)], "104", True)
    assert outs[0]["world_vx"] == 150
    assert outs[0]["world_vz"] == 0


def test_reset_clears_prev() -> None:
    et = EnemyMotionTracker()
    pt = PlayerMotionTracker()
    et.update([_zombie(0, 10000, 10000)], "104", True)
    pt.update(10000, 10000, "104", True)
    et.reset()
    pt.reset()
    assert et.prev == {}
    assert pt.prev is None
    outs = et.update([_zombie(0, 10200, 10000)], "104", True)
    assert outs[0]["world_vx"] == 0
    assert outs[0]["world_vz"] == 0
    assert pt.update(10200, 10000, "104", True) == (0, 0)
