"""Crop pillarbox then AREA-resize game plane to 84x63 (4:3)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.env import FRAME_H, FRAME_SHAPE, FRAME_W, PILLARBOX_LEFT, _resize_frame
from re1_rl.frame_ring import GAME_H, GAME_W, crop_game_plane


def test_crop_game_plane_drops_bars() -> None:
    rgb = np.zeros((240, 350, 3), dtype=np.uint8)
    rgb[:, PILLARBOX_LEFT : PILLARBOX_LEFT + GAME_W, :] = 200
    crop = crop_game_plane(rgb)
    assert crop.shape == (GAME_H, GAME_W, 3)
    assert float(crop.mean()) == 200.0


def test_resize_frame_outputs_63x84x1() -> None:
    rgb = np.random.randint(0, 255, (240, 350, 3), dtype=np.uint8)
    out = _resize_frame(rgb)
    assert out.shape == (FRAME_H, FRAME_W, 1)
    assert out.dtype == np.uint8
    assert FRAME_H == 63 and FRAME_W == 84


def test_frame_shape_constant() -> None:
    assert FRAME_SHAPE == (63, 84, 4)


def test_resize_removes_bar_bleed() -> None:
    """Bars black, content bright — edge columns must stay bright (no bar bleed)."""
    rgb = np.zeros((240, 350, 3), dtype=np.uint8)
    rgb[:, PILLARBOX_LEFT : PILLARBOX_LEFT + GAME_W, :] = 200
    out = _resize_frame(rgb)[..., 0]
    assert out.shape == (63, 84)
    assert float(out.mean()) > 190.0
    assert float(out[:, 0].mean()) > 190.0
    assert float(out[:, -1].mean()) > 190.0
