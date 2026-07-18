"""Tests for emulator-frame ring and attack pin stacks."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from re1_rl.frame_ring import AttackFramePins, FrameRingBuffer, FRAME_SHAPE


def test_stack_at_stride_offsets() -> None:
    ring = FrameRingBuffer()
    stride = FrameRingBuffer.STRIDE
    frames = tuple(stride * i for i in range(1, 5))
    for fc in frames:
        ring.store_plane(fc, np.full((84, 77, 1), fc, dtype=np.uint8))
    stack = ring.stack_at(frames[-1])
    assert stack.shape == FRAME_SHAPE
    assert int(stack[0, 0, 0]) == frames[0]
    assert int(stack[0, 0, 1]) == frames[1]
    assert int(stack[0, 0, 2]) == frames[2]
    assert int(stack[0, 0, 3]) == frames[3]


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


def test_attack_pins_macro_anim_history() -> None:
    pins = AttackFramePins()
    pins.entry_hooks = (0x10, 0x00, 0)
    pins.windup_hooks = (0x12, 0x04, 10)
    pins.swing_hooks = (0x13, 0x04, 5)
    pins.end_hooks = (0x00, 0x00, 0)
    pins.end = np.zeros((84, 77, 1), dtype=np.uint8)
    pins.entry = pins.end
    hist = pins.macro_anim_history()
    assert hist == [
        (0x10, 0x00, 0),
        (0x12, 0x04, 10),
        (0x13, 0x04, 5),
        (0x00, 0x00, 0),
    ]


def test_attack_pins_after_frame_screenshots_only_on_first_swing() -> None:
    """Macros must not MMF-capture every STRIDE frame — only the swing pin."""
    shots = {"n": 0}

    def _shot() -> np.ndarray:
        shots["n"] += 1
        return np.full((240, 320, 3), shots["n"], dtype=np.uint8)

    ring = FrameRingBuffer()
    bridge = SimpleNamespace(
        screenshot=_shot,
        emulated_frame=0,
        frame_ring=ring,
        read_ram=lambda _fields: {
            "player_anim": 0x14,
            "player_action_aux": 0x04,
            "player_recovery_timer": 0,
        },
    )
    pins = AttackFramePins()
    pins.begin(bridge)
    assert shots["n"] == 1
    # Non-swing / already-pinned frames must not screenshot.
    for fc in (4, 8, 12):
        bridge.emulated_frame = fc
        pins.after_frame(bridge, is_swing=False)
    assert shots["n"] == 1
    bridge.emulated_frame = 16
    pins.after_frame(bridge, is_swing=True, hooks=(0x14, 0x04, 0))
    assert shots["n"] == 2
    assert pins.swing is not None
    # Second swing frame must not re-capture.
    bridge.emulated_frame = 20
    pins.after_frame(bridge, is_swing=True, hooks=(0x14, 0x04, 0))
    assert shots["n"] == 2
    pins.finish(bridge)
    assert shots["n"] == 3
    # Windup falls back to entry when mid-macro stride pins are disabled.
    assert pins.windup is pins.entry
