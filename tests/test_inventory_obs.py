"""Unit tests for on-person inventory obs (A1)."""

from __future__ import annotations

import numpy as np
import pytest

from re1_rl.obs_encoder import INVENTORY_OBS_DIM, INVENTORY_SLOTS, encode_inventory_slots
from re1_rl.memory_map import decode_inventory, decode_inventory_slots
from re1_rl.weapon_damage import AMMO_QTY_NORM


def test_inventory_obs_dim() -> None:
    assert INVENTORY_OBS_DIM == INVENTORY_SLOTS * 2


def test_empty_inventory_is_zeros() -> None:
    v = encode_inventory_slots([])
    assert v.shape == (INVENTORY_OBS_DIM,)
    assert np.all(v == 0.0)


def test_inventory_encodes_item_id_and_qty() -> None:
    v = encode_inventory_slots([("shield_key", 1), ("emblem", 1)])
    # slot 0: shield_key 0x35
    assert v[0] == pytest.approx(0x35 / 0x4B)
    assert v[1] == pytest.approx(1 / AMMO_QTY_NORM)
    # slot 1: emblem 0x1F
    assert v[2] == pytest.approx(0x1F / 0x4B)
    assert v[3] == pytest.approx(1 / AMMO_QTY_NORM)
    # remaining slots zero
    assert np.all(v[4:] == 0.0)


def test_inventory_caps_at_eight_slots() -> None:
    slots = [("shield_key", 1)] * 12
    v = encode_inventory_slots(slots)
    assert v.shape == (16,)
    assert v[0] > 0
    assert v[14] > 0  # slot 7 item_id
    assert v[15] == pytest.approx(1 / AMMO_QTY_NORM)  # slot 7 qty — 9th+ inputs dropped


def test_inventory_preserves_interior_ram_gap() -> None:
    v = encode_inventory_slots(
        [("knife", 0), ("", 0), ("beretta", 12), ("", 0)]
    )
    assert v[2:4].tolist() == [0.0, 0.0]
    assert v[4] == pytest.approx(0x02 / 0x4B)
    assert v[5] == pytest.approx(12 / AMMO_QTY_NORM)


def test_ram_decode_keeps_aligned_slots_but_occupied_view_stays_compact() -> None:
    ram = {
        "inv_slot_0": 0x0001,
        "inv_slot_1": 0,
        "inv_slot_2": 0x0C02,
    }
    aligned = decode_inventory_slots(ram)
    assert len(aligned) == 8
    assert aligned[:3] == [("knife", 0), ("", 0), ("beretta", 12)]
    assert decode_inventory(ram) == [("knife", 0), ("beretta", 12)]
