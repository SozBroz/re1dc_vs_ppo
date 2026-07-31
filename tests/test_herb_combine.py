"""RE1 Director's Cut herb combine rules and 3-step menu actions."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import COMBINE_ACTION, SELECT_SLOT_BASE, action_mask
from re1_rl.herb_combine import (
    BLUE,
    GREEN,
    MIXED_GB,
    MIXED_GG,
    MIXED_GGB,
    MIXED_GGG,
    MIXED_GR,
    MIXED_GRB,
    RED,
    can_combine_slots,
    combine_product,
    combine_totals,
    index_to_pair,
    pair_to_index,
    plan_combine,
)
from re1_rl.inventory_combine import apply_combine, plan_combine as plan_any_combine

N_ACTIONS = 45


def _inv(*slots: tuple[int, int]) -> list[tuple[int, int]]:
    out = list(slots)
    while len(out) < 8:
        out.append((0, 0))
    return out


def test_pair_index_roundtrip() -> None:
    for i in range(8):
        for j in range(i + 1, 8):
            idx = pair_to_index(i, j)
            assert index_to_pair(idx) == (i, j)


def test_valid_base_combinations() -> None:
    assert combine_product(GREEN, RED) == MIXED_GR
    assert combine_product(GREEN, GREEN) == MIXED_GG
    assert combine_product(GREEN, BLUE) == MIXED_GB


def test_plan_combine_writes_lower_slot() -> None:
    inv = _inv((0x01, 0), (GREEN, 1), (RED, 1))
    planned = plan_combine(inv, 1, 2)
    assert planned is not None
    new_inv, dest, product = planned
    assert dest == 1
    assert product == MIXED_GR


class _FakeBridge:
    def __init__(self, inv: list[tuple[int, int]]) -> None:
        self._inv = list(inv)
        self.writes: list[list] = []

    def read_block(self, address: int, count: int) -> list[int]:
        from re1_rl.memory_map import INVENTORY_BASE

        assert address == INVENTORY_BASE
        raw: list[int] = []
        for item_id, qty in self._inv:
            raw.extend([item_id, qty])
        return raw[:count]

    def read_ram(self, fields):
        return {"equipped_weapon_id": 0, "equipped_slot_1based": 0}

    def write_ram(self, fields):
        self.writes.append(fields)
        for name, _addr, _dtype, value in fields:
            if name.startswith("inv_"):
                idx = int(name.split("_", 1)[1])
                item_id = int(value) & 0xFF
                qty = (int(value) >> 8) & 0xFF
                self._inv[idx] = (item_id, qty)


def test_apply_combine_herb_ram_write() -> None:
    inv = _inv((GREEN, 1), (RED, 1))
    bridge = _FakeBridge(inv)
    result = apply_combine(
        bridge, 0, 1, equipped_weapon_id=0, equipped_slot_0based=None,
    )
    assert result["ok"]
    assert result["product"] == MIXED_GR
    assert bridge._inv[0] == (MIXED_GR, 1)
    assert bridge._inv[1] == (0, 0)


def test_combine_action_mask_phases() -> None:
    inv = _inv((GREEN, 1), (RED, 1))
    m0 = action_mask(N_ACTIONS, None, inventory=inv, combine_phase=0)
    assert m0[COMBINE_ACTION]
    m1 = action_mask(N_ACTIONS, None, inventory=inv, combine_phase=1)
    assert m1[SELECT_SLOT_BASE]
    m2 = action_mask(N_ACTIONS, None, inventory=inv, combine_phase=2, combine_slot_a=0)
    assert m2[SELECT_SLOT_BASE + 1]


def test_action_layout_includes_combine_menu() -> None:
    from re1_rl.env import ACTION_NAMES

    assert len(ACTION_NAMES) == N_ACTIONS
    assert ACTION_NAMES[COMBINE_ACTION] == "combine"
