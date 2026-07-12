"""Full-frame 84x84 resize, then prune pillarbox columns -> 84x77."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.env import (
    FRAME_H,
    FRAME_SHAPE,
    FRAME_SQUARE,
    FRAME_W,
    PILLARBOX_LEFT_SQ,
    PILLARBOX_RIGHT_SQ,
    _prune_square_pillarbox,
    _resize_frame,
)


def test_prune_square_pillarbox_4_3() -> None:
    square = np.zeros((FRAME_SQUARE, FRAME_SQUARE), dtype=np.uint8)
    square[:, PILLARBOX_LEFT_SQ : FRAME_SQUARE - PILLARBOX_RIGHT_SQ] = 128
    pruned = _prune_square_pillarbox(square)
    assert pruned.shape == (FRAME_SQUARE, FRAME_W)
    assert FRAME_W == 77
    assert PILLARBOX_LEFT_SQ == 4 and PILLARBOX_RIGHT_SQ == 3
    assert int(pruned.min()) == 128


def test_resize_frame_outputs_84x77x1() -> None:
    rgb = np.random.randint(0, 255, (240, 350, 3), dtype=np.uint8)
    out = _resize_frame(rgb)
    assert out.shape == (FRAME_H, FRAME_W, 1)
    assert out.dtype == np.uint8


def test_frame_shape_constant() -> None:
    assert FRAME_SHAPE == (84, 77, 4)


def test_prune_removes_bar_dilution() -> None:
    """Bars black, content bright on raw 350 — after square+prune, mean stays high."""
    rgb = np.zeros((240, 350, 3), dtype=np.uint8)
    rgb[:, 18 : 350 - 12, :] = 200
    out = _resize_frame(rgb)
    assert out.shape == (84, 77, 1)
    assert float(out.mean()) > 150.0
