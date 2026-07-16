"""Tests for emulator-frame ring and attack pin stacks."""

from __future__ import annotations

import numpy as np

from re1_rl.frame_ring import (
    AnimRingBuffer,
    AttackFramePins,
    FrameRingBuffer,
    FRAME_SHAPE,
)


def test_stack_at_stride_offsets() -> None:
    ring = FrameRingBuffer()
    for fc in (4, 8, 12, 16):
        ring.store_plane(fc, np.full((84, 77, 1), fc, dtype=np.uint8))
    stack = ring.stack_at(16)
    assert stack.shape == FRAME_SHAPE
    assert int(stack[0, 0, 0]) == 4
    assert int(stack[0, 0, 1]) == 8
    assert int(stack[0, 0, 2]) == 12
    assert int(stack[0, 0, 3]) == 16


def test_anim_history_at_stride_offsets() -> None:
    ring = AnimRingBuffer()
    samples = [
        (4, (0x10, 0x01, 4)),
        (8, (0x12, 0x04, 8)),
        (12, (0x13, 0x04, 3)),
        (16, (0x14, 0x03, 0)),
    ]
    for fc, hooks in samples:
        ring.store(fc, hooks)
    hist = ring.history_at(16)
    assert hist == [s[1] for s in samples]


def test_attack_pins_stack_order() -> None:
    ring = FrameRingBuffer()
    pins = AttackFramePins()
    pins.entry = np.full((84, 77, 1), 1, dtype=np.uint8)
    pins.windup = np.full((84, 77, 1), 2, dtype=np.uint8)
    pins.swing = np.full((84, 77, 1), 3, dtype=np.uint8)
    pins.end = np.full((84, 77, 1), 4, dtype=np.uint8)
    stack = pins.stack_hwc(ring, end_frame=40)
    assert int(stack[0, 0, 0]) == 1
    assert int(stack[0, 0, 1]) == 2
    assert int(stack[0, 0, 2]) == 3
    assert int(stack[0, 0, 3]) == 4


def test_attack_pins_anim_history_order() -> None:
    ring = AnimRingBuffer()
    pins = AttackFramePins()
    pins.entry_hooks = (0x10, 0x00, 0)
    pins.windup_hooks = (0x12, 0x04, 10)
    pins.swing_hooks = (0x13, 0x04, 5)
    pins.end_hooks = (0x00, 0x00, 0)
    pins.end = np.zeros((84, 77, 1), dtype=np.uint8)
    pins.entry = pins.end
    hist = pins.anim_history(ring, end_frame=40)
    assert hist == [
        (0x10, 0x00, 0),
        (0x12, 0x04, 10),
        (0x13, 0x04, 5),
        (0x00, 0x00, 0),
    ]
