"""Emulator-frame ring: obs channels are t-12, t-8, t-4, t (HWC axis=-1)."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# BizHawk RE1 screenshot: 240x350 RGB with near-black pillarbox.
# Crop the authentic 320x240 game plane (4:3), then AREA-resize to 84x63
# (OpenCV WxH) → numpy (FRAME_H, FRAME_W) = (63, 84).
PILLARBOX_LEFT = 18
PILLARBOX_RIGHT = 12
GAME_W = 320
GAME_H = 240
FRAME_W = 84  # width (4:3 landscape with height 63)
FRAME_H = 63  # height
FRAME_STACK = 4
FRAME_SHAPE = (FRAME_H, FRAME_W, FRAME_STACK)
# Legacy aliases (old square+prune path removed).
FRAME_SQUARE = FRAME_W
PILLARBOX_LEFT_SQ = 0
PILLARBOX_RIGHT_SQ = 0
AnimHooks = tuple[int, int, int]
ZERO_HOOKS: AnimHooks = (0, 0, 0)


def crop_game_plane(frame: np.ndarray) -> np.ndarray:
    """Crop BizHawk RGB/gray to the 320x240 game plane (drop pillarbox)."""
    h, w = frame.shape[:2]
    x0 = PILLARBOX_LEFT
    x1 = x0 + GAME_W
    if h < GAME_H or w < x1:
        # Already a game plane, or unexpected size — best-effort.
        if h == GAME_H and w == GAME_W:
            return frame
        return frame[:GAME_H, : min(w, GAME_W)]
    return frame[:GAME_H, x0:x1]


def prune_square_pillarbox(square: np.ndarray) -> np.ndarray:
    """Deprecated no-op kept for import compatibility; prefer crop_game_plane."""
    return square


def resize_rgb_to_plane(
    frame: np.ndarray, size: tuple[int, int] | None = None
) -> np.ndarray:
    """RGB → crop pillarbox → grayscale → INTER_AREA to (FRAME_W, FRAME_H)."""
    import cv2

    if size is None:
        size = (FRAME_W, FRAME_H)  # OpenCV (width, height)
    game = crop_game_plane(frame)
    gray = cv2.cvtColor(game, cv2.COLOR_RGB2GRAY)
    plane = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    return plane[..., None]


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

    # Emulated frames between stack planes; match RE1Env.frame_skip baseline.
    STRIDE = 8

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

    def after_frame(
        self,
        bridge: Any,
        *,
        is_swing: bool,
        hooks: AnimHooks | None = None,
    ) -> None:
        """Pin only the first swing frame (plus begin/finish).

        Training used to also screenshot every ``STRIDE`` frames here — that
        duplicated MMF captures for ring fill while the obs stack already comes
        from entry/windup/swing/end pins. Skip non-swing frames entirely.
        """
        if not self.active:
            return
        if not (is_swing and self.swing is None):
            return
        fc = int(getattr(bridge, "emulated_frame", -1))
        ring = getattr(bridge, "frame_ring", None)
        plane = resize_rgb_to_plane(bridge.screenshot())
        pinned_hooks = hooks if hooks is not None else self._read_hooks(bridge)
        if ring is not None and fc >= 0:
            ring.store_plane(fc, plane)
        self.swing = plane
        self.windup = self._prev if self._prev is not None else self.entry
        self.swing_hooks = pinned_hooks
        self.windup_hooks = (
            self._prev_hooks if self._prev_hooks is not None else self.entry_hooks
        )
        self._prev = plane
        self._prev_hooks = pinned_hooks

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
