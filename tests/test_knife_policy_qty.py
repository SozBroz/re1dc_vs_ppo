"""Knife inventory qty normalization for policy/mask (RAM unchanged)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.weapon_equip import (
    POLICY_KNIFE_QTY,
    policy_inventory,
    policy_item_qty,
    slot_legal_for_equip,
)


def test_knife_qty_zero_treated_as_owned_for_policy() -> None:
    assert policy_item_qty(0x01, 0) == POLICY_KNIFE_QTY
    assert policy_item_qty(0x02, 0) == 0
    assert policy_item_qty(0x01, 3) == 3


def test_policy_inventory_normalizes_knife_only() -> None:
    raw = [(0x01, 0), (0x02, 15)] + [(0, 0)] * 6
    view = policy_inventory(raw)
    assert view[0] == (0x01, POLICY_KNIFE_QTY)
    assert view[1] == (0x02, 15)


def test_equip_gun_to_knife_when_ram_qty_zero() -> None:
    inv = [(0x01, 0), (0x02, 15)] + [(0, 0)] * 6
    assert slot_legal_for_equip(
        inv, 0, equipped_weapon_id=0x02, equipped_slot_0based=1
    )
    assert not slot_legal_for_equip(
        inv, 1, equipped_weapon_id=0x02, equipped_slot_0based=1
    )
