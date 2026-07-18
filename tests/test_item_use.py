"""RE1 DC inventory USE (consume heal items)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import (
    COMBINE_ACTION,
    SELECT_SLOT_BASE,
    USE_ACTION,
    action_mask,
)
from re1_rl.herb_combine import GREEN, MIXED_GR, RED
from re1_rl.item_use import apply_use, is_usable_item, plan_use
from re1_rl.memory_map import INVENTORY_BASE, PLAYER_HP, PLAYER_HP_MAX

N_ACTIONS = 46


def _inv(*slots: tuple[int, int]) -> list[tuple[int, int]]:
    out = list(slots)
    while len(out) < 8:
        out.append((0, 0))
    return out


def test_red_herb_not_usable() -> None:
    assert not is_usable_item(RED)


def test_green_heal_amount() -> None:
    inv = _inv((GREEN, 1))
    planned = plan_use(inv, 0, current_hp=50)
    assert planned is not None
    new_inv, heal, cured, item_id = planned
    assert heal == 25
    assert not cured
    assert item_id == GREEN
    assert new_inv[0] == (0, 0)


def test_mixed_gr_heals_seventy() -> None:
    inv = _inv((MIXED_GR, 1))
    planned = plan_use(inv, 0, current_hp=10)
    assert planned is not None
    assert planned[1] == 70


def test_spray_full_heal_capped() -> None:
    inv = _inv((0x41, 1))
    planned = plan_use(inv, 0, current_hp=PLAYER_HP_MAX - 10)
    assert planned is not None
    assert planned[1] == 10


def test_mask_only_noop_when_not_in_control() -> None:
    m = action_mask(N_ACTIONS, None, in_control=False)
    assert m[0]
    assert m[1:].sum() == 0


def test_use_mask_two_step() -> None:
    inv = _inv((0x01, 0), (0x44, 1), (RED, 1))
    m0 = action_mask(
        N_ACTIONS, None, inventory=inv,
        player_anim=0, player_aux=0, player_recovery=0,
        current_hp=100,
    )
    assert m0[USE_ACTION]
    assert not m0[SELECT_SLOT_BASE + 1]  # phase 0
    m1 = action_mask(
        N_ACTIONS, None, inventory=inv, use_phase=1, current_hp=100,
    )
    assert m1[SELECT_SLOT_BASE + 1]
    assert not m1[SELECT_SLOT_BASE + 2]  # red not usable


def test_use_masked_at_fine_episode_hp() -> None:
    inv = _inv((0x41, 1), (0x44, 1))
    m = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=96,
        episode_start_hp=96,
        poisoned=False,
    )
    assert not m[USE_ACTION]


def test_use_masked_at_full_hp() -> None:
    from re1_rl.memory_map import PLAYER_HP_MAX

    inv = _inv((0x41, 1), (0x44, 1))
    m = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=PLAYER_HP_MAX,
        episode_start_hp=96,
        poisoned=False,
    )
    assert not m[USE_ACTION]


def test_combine_not_legal_at_jill_start() -> None:
    from re1_rl.action_mask import COMBINE_ACTION

    inv = _inv((0x01, 0), (0x02, 15), (0x41, 1))
    m = action_mask(N_ACTIONS, None, inventory=inv, current_hp=96)
    assert not m[COMBINE_ACTION]
    assert not m[SELECT_SLOT_BASE:SELECT_SLOT_BASE + 8].any()


def test_use_blue_legal_when_poisoned_at_full_hp() -> None:
    from re1_rl.herb_combine import BLUE
    from re1_rl.memory_map import PLAYER_HP_MAX

    inv = _inv((BLUE, 1))
    m = action_mask(
        N_ACTIONS,
        None,
        inventory=inv,
        current_hp=PLAYER_HP_MAX,
        poisoned=True,
        use_phase=1,
    )
    assert m[SELECT_SLOT_BASE]


class _FakeBridge:
    def __init__(self, inv: list[tuple[int, int]], hp: int = 50) -> None:
        self._inv = list(inv)
        self._hp = hp
        self.writes: list[list] = []

    def read_block(self, address: int, count: int) -> list[int]:
        assert address == INVENTORY_BASE
        raw: list[int] = []
        for item_id, qty in self._inv:
            raw.extend([item_id, qty])
        return raw[:count]

    def read_ram(self, fields):
        out = {}
        for name, _addr, _dtype in fields:
            if name == "player_hp":
                out[name] = self._hp
            elif name.startswith("equipped"):
                out[name] = 0
        return out

    def write_ram(self, fields):
        self.writes.append(fields)
        for name, _addr, _dtype, value in fields:
            if name == "player_hp":
                self._hp = int(value)
            elif name.startswith("inv_"):
                idx = int(name.split("_", 1)[1])
                item_id = int(value) & 0xFF
                qty = (int(value) >> 8) & 0xFF
                self._inv[idx] = (item_id, qty)


def test_apply_use_writes_hp_and_clears_slot() -> None:
    bridge = _FakeBridge(_inv((GREEN, 1)), hp=40)
    result = apply_use(bridge, 0)
    assert result["ok"]
    assert result["heal_applied"] == 25
    assert bridge._hp == 65
    assert bridge._inv[0] == (0, 0)


def test_action_layout_includes_use() -> None:
    from re1_rl.env import ACTION_NAMES

    assert len(ACTION_NAMES) == N_ACTIONS
    assert ACTION_NAMES[USE_ACTION] == "use"
    assert ACTION_NAMES[COMBINE_ACTION] == "combine"
