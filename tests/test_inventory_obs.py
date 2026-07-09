"""Unit tests for on-person inventory obs (A1)."""

from __future__ import annotations

import numpy as np

from re1_rl.obs_encoder import INVENTORY_OBS_DIM, INVENTORY_SLOTS, encode_inventory_slots


def test_inventory_obs_dim() -> None:
    assert INVENTORY_OBS_DIM == INVENTORY_SLOTS * 2


def test_empty_inventory_is_zeros() -> None:
    v = encode_inventory_slots([])
    assert v.shape == (INVENTORY_OBS_DIM,)
    assert np.all(v == 0.0)


def test_inventory_encodes_item_id_and_qty() -> None:
    v = encode_inventory_slots([("shield_key", 1), ("emblem", 1)])
    # slot 0: shield_key 0x35
    assert v[0] == 0x35 / 0x4B
    assert v[1] == 1 / 15.0
    # slot 1: emblem 0x1F
    assert v[2] == 0x1F / 0x4B
    assert v[3] == 1 / 15.0
    # remaining slots zero
    assert np.all(v[4:] == 0.0)


def test_inventory_caps_at_eight_slots() -> None:
    slots = [("shield_key", 1)] * 12
    v = encode_inventory_slots(slots)
    assert v.shape == (16,)
    assert v[0] > 0
    assert v[14] > 0  # slot 7 item_id
    assert v[15] == 1 / 15.0  # slot 7 qty — 9th+ inputs dropped
