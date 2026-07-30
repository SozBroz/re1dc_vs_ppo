"""Unit tests for enemy-motion checkpoint column remaps (no BizHawk)."""

from __future__ import annotations

import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from transplant_enemy_motion import (  # noqa: E402
    OLD_ENEMY_SLOT_FIELDS,
    NEW_ENEMY_SLOT_FIELDS,
    OLD_PROPRIO_DELTA,
    OLD_SPATIAL_DELTA,
    remap_control_weight,
    remap_spatial_weight,
    _enemies_start_index,
)


def test_remap_spatial_weight_preserves_prefix_and_zeros_velocity() -> None:
    from re1_rl.spatial_encoder import ENEMY_SLOTS, SPATIAL_DIM

    old_dim = SPATIAL_DIM - OLD_SPATIAL_DELTA
    rows = 4
    old_w = torch.arange(rows * old_dim, dtype=torch.float32).reshape(rows, old_dim)
    new_w = torch.ones(rows, SPATIAL_DIM)
    remap_spatial_weight(old_w, new_w)

    start = _enemies_start_index()
    assert torch.equal(new_w[:, :start], old_w[:, :start])

    for slot in range(ENEMY_SLOTS):
        o0 = start + slot * OLD_ENEMY_SLOT_FIELDS
        n0 = start + slot * NEW_ENEMY_SLOT_FIELDS
        assert torch.equal(
            new_w[:, n0 : n0 + OLD_ENEMY_SLOT_FIELDS],
            old_w[:, o0 : o0 + OLD_ENEMY_SLOT_FIELDS],
        )
        assert torch.count_nonzero(new_w[:, n0 + OLD_ENEMY_SLOT_FIELDS : n0 + NEW_ENEMY_SLOT_FIELDS]) == 0

    old_suffix = start + ENEMY_SLOTS * OLD_ENEMY_SLOT_FIELDS
    new_suffix = start + ENEMY_SLOTS * NEW_ENEMY_SLOT_FIELDS
    assert torch.equal(new_w[:, new_suffix:], old_w[:, old_suffix:])


def test_remap_control_weight_zeros_player_velocity_cols() -> None:
    from re1_rl.doc04_medium_extractor import ROOM_EMBED_DIM
    from re1_rl.obs_encoder import PROPRIO_DIM

    old_in = (PROPRIO_DIM - OLD_PROPRIO_DELTA) - 1 + ROOM_EMBED_DIM
    new_in = PROPRIO_DIM - 1 + ROOM_EMBED_DIM
    rows = 3
    old_w = torch.randn(rows, old_in)
    new_w = torch.ones(rows, new_in)
    remap_control_weight(old_w, new_w)

    old_scalar = old_in - ROOM_EMBED_DIM
    new_scalar = new_in - ROOM_EMBED_DIM
    assert torch.equal(new_w[:, :old_scalar], old_w[:, :old_scalar])
    assert torch.count_nonzero(new_w[:, old_scalar:new_scalar]) == 0
    assert torch.equal(new_w[:, new_scalar:], old_w[:, old_scalar:])
