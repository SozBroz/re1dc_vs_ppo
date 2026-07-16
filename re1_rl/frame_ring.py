"""Emulator-frame ring: obs channels are t-12, t-8, t-4, t (HWC axis=-1)."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Match RE1Env frame pipeline (84x84 square -> prune pillarbox -> 77 wide).
PILLARBOX_LEFT = 18
PILLARBOX_RIGHT = 12
FRAME_SQUARE = 84
PILLARBOX_LEFT_SQ = round(PILLARBOX_LEFT * FRAME_SQUARE / 350)
PILLARBOX_RIGHT_SQ = round(PILLARBOX_RIGHT * FRAME_SQUARE / 350)
FRAME_H = FRAME_SQUARE
FRAME_W = FRAME_SQUARE - PILLARBOX_LEFT_SQ - PILLARBOX_RIGHT_SQ
FRAME_STACK = 4
FRAME_SHAPE = (FRAME_H, FRAME_W, FRAME_STACK)
AnimHooks = tuple[int, int, int]
ZERO_HOOKS: AnimHooks = (0, 0, 0)


def prune_square_pillarbox(square: np.ndarray) -> np.ndarray:
    w = int(square.shape[1])
    if w != FRAME_SQUARE:
        return square
    return square[:, PILLARBOX_LEFT_SQ : FRAME_SQUARE - PILLARBOX_RIGHT_SQ]


def resize_rgb_to_plane(
    frame: np.ndarray, size: tuple[int, int] = (FRAME_SQUARE, FRAME_SQUARE)
) -> np.ndarray:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    square = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    pruned = prune_square_pillarbox(square)
    return pruned[..., None]


def decode_png_b64(png_b64: str) -> np.ndarray:
    import cv2

    raw = base64.b64decode(png_b64)
    arr = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("Failed to decode ring PNG base64")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


class FrameRingBuffer:
    """Sparse store of grayscale planes keyed by emulated frame index."""

    STRIDE = 4

    def __init__(self) -> None:
        self._planes: dict[int, np.ndarray] = {}
        self._latest_frame: int | None = None

    def clear(self) -> None:
        self._planes.clear()
        self._latest_frame = None

    def note_frame(self, frame_count: int) -> None:
        if frame_count >= 0:
            self._latest_frame = int(frame_count)

    def store_rgb(self, frame_count: int, rgb: np.ndarray) -> None:
        if frame_count < 0:
            return
        self._planes[int(frame_count)] = resize_rgb_to_plane(rgb)
        self._latest_frame = int(frame_count)

    def store_plane(self, frame_count: int, plane: np.ndarray) -> None:
        if frame_count < 0:
            return
        self._planes[int(frame_count)] = plane
        self._latest_frame = int(frame_count)

    def plane_at(self, frame_count: int) -> np.ndarray | None:
        if frame_count in self._planes:
            return self._planes[frame_count]
        # Nearest captured frame at or before the requested index.
        best: int | None = None
        for fc in self._planes:
            if fc <= frame_count and (best is None or fc > best):
                best = fc
        if best is not None:
            return self._planes[best]
        # Fall back to earliest capture after (cold start).
        for fc in sorted(self._planes):
            return self._planes[fc]
        return None

    def stack_at(self, frame_count: int) -> np.ndarray:
        """Channels-last [t-12, t-8, t-4, t]."""
        keys = (
            frame_count - 3 * self.STRIDE,
            frame_count - 2 * self.STRIDE,
            frame_count - self.STRIDE,
            frame_count,
        )
        planes: list[np.ndarray] = []
        fallback = np.zeros((FRAME_H, FRAME_W, 1), dtype=np.uint8)
        for key in keys:
            plane = self.plane_at(key)
            planes.append(plane if plane is not None else fallback)
        # If everything was empty, avoid four zero planes when we have any history.
        if not self._planes:
            return np.zeros(FRAME_SHAPE, dtype=np.uint8)
        # Fill leading gaps from the oldest available sample.
        first = self.plane_at(min(self._planes))
        if first is not None:
            for i, plane in enumerate(planes):
                if plane is fallback and np.count_nonzero(plane) == 0:
                    planes[i] = first
        return np.concatenate(planes, axis=-1)


@dataclass
class AttackFramePins:
    """Event captures for attack / knife macros: entry -> windup -> swing -> end."""

    entry: np.ndarray | None = None
    windup: np.ndarray | None = None
    swing: np.ndarray | None = None
    end: np.ndarray | None = None
    entry_hooks: AnimHooks | None = None
    windup_hooks: AnimHooks | None = None
    swing_hooks: AnimHooks | None = None
    end_hooks: AnimHooks | None = None
    _prev: np.ndarray | None = field(default=None, repr=False)
    _prev_hooks: AnimHooks | None = field(default=None, repr=False)
    active: bool = False

    def clear(self) -> None:
        self.entry = None
        self.windup = None
        self.swing = None
        self.end = None
        self.entry_hooks = None
        self.windup_hooks = None
        self.swing_hooks = None
        self.end_hooks = None
        self._prev = None
        self._prev_hooks = None
        self.active = False

    def _read_hooks(self, bridge: Any) -> AnimHooks:
        from re1_rl.knife_macro import read_knife_hooks

        try:
            return read_knife_hooks(bridge)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            return ZERO_HOOKS

    def begin(self, bridge: Any) -> None:
        self.clear()
        self.active = True
        self.entry = resize_rgb_to_plane(bridge.screenshot())
        self.entry_hooks = self._read_hooks(bridge)

    def after_frame(self, bridge: Any, *, is_swing: bool) -> None:
        if not self.active:
            return
        fc = int(getattr(bridge, "emulated_frame", -1))
        ring = getattr(bridge, "frame_ring", None)
        capture = bool(is_swing and self.swing is None)
        if not capture and fc >= 0 and fc % FrameRingBuffer.STRIDE == 0:
            capture = True
        if not capture:
            return
        plane = resize_rgb_to_plane(bridge.screenshot())
        hooks = self._read_hooks(bridge)
        if ring is not None and fc >= 0:
            ring.store_plane(fc, plane)
        if is_swing and self.swing is None:
            self.swing = plane
            self.windup = self._prev if self._prev is not None else self.entry
            self.swing_hooks = hooks
            self.windup_hooks = (
                self._prev_hooks if self._prev_hooks is not None else self.entry_hooks
            )
        self._prev = plane
        self._prev_hooks = hooks

    def finish(self, bridge: Any) -> None:
        if not self.active:
            return
        self.end = resize_rgb_to_plane(bridge.screenshot())
        self.end_hooks = self._read_hooks(bridge)
        fc = int(getattr(bridge, "emulated_frame", -1))
        ring = getattr(bridge, "frame_ring", None)
        if ring is not None and fc >= 0:
            ring.store_plane(fc, self.end)
        self.active = False

    def ready(self) -> bool:
        return self.end is not None and self.entry is not None

    def stack_hwc(self, ring: FrameRingBuffer, end_frame: int) -> np.ndarray:
        """Build [entry, windup, swing, end] with ring fallbacks for missing pins."""
        end_p = self.end if self.end is not None else ring.plane_at(end_frame)
        swing_p = (
            self.swing
            if self.swing is not None
            else ring.plane_at(end_frame - FrameRingBuffer.STRIDE)
        )
        wind_p = self.windup
        if wind_p is None:
            wind_p = ring.plane_at(end_frame - 2 * FrameRingBuffer.STRIDE)
        if wind_p is None:
            wind_p = self.entry
        entry_p = self.entry if self.entry is not None else wind_p
        planes = [entry_p, wind_p, swing_p, end_p]
        fallback = np.zeros((FRAME_H, FRAME_W, 1), dtype=np.uint8)
        out: list[np.ndarray] = []
        for plane in planes:
            out.append(plane if plane is not None else fallback)
        return np.concatenate(out, axis=-1)

    def macro_anim_history(self) -> list[AnimHooks]:
        """Four anim samples aligned with macro frame pins: entry, windup, swing, end."""
        if not self.ready():
            return [ZERO_HOOKS] * FRAME_STACK
        wind_h = self.windup_hooks if self.windup_hooks is not None else self.entry_hooks
        swing_h = self.swing_hooks if self.swing_hooks is not None else wind_h
        entry_h = self.entry_hooks if self.entry_hooks is not None else wind_h
        end_h = self.end_hooks if self.end_hooks is not None else swing_h
        hooks = [entry_h, wind_h, swing_h, end_h]
        return [h if h is not None else ZERO_HOOKS for h in hooks]
