"""Inventory COMBINE: ammo merge, weapon reload, and attack ammo masks."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import ATTACK_ACTION, COMBINE_ACTION, EQUIP_ACTION, SELECT_SLOT_BASE, action_mask
from re1_rl.ammo_accounting import can_fire_weapon, total_fireable_ammo
from re1_rl.inventory_combine import apply_combine, plan_combine

N_ACTIONS = 45


def _inv(*slots: tuple[int, int]) -> list[tuple[int, int]]:
    out = list(slots)
    while len(out) < 8:
        out.append((0, 0))
    return out


def test_ammo_pile_merge_ordered() -> None:
    inv = _inv((0x0B, 40), (0x0B, 15))
    planned = plan_combine(inv, 0, 1)
    assert planned is not None
    new_inv, dest, product = planned
    assert dest == 0
    assert product == 0x0B
    assert new_inv[0] == (0x0B, 55)
    assert new_inv[1] == (0, 0)


def test_ammo_pile_merge_respects_cap() -> None:
    inv = _inv((0x0B, 50), (0x0B, 20))
    planned = plan_combine(inv, 0, 1)
    assert planned is not None
    new_inv, _, _ = planned
    assert new_inv[0] == (0x0B, 60)
    assert new_inv[1] == (0x0B, 10)


def test_beretta_reload_from_handgun_bullets() -> None:
    inv = _inv((0x02, 0), (0x0B, 30))
    planned = plan_combine(inv, 0, 1)
    assert planned is not None
    new_inv, dest, product = planned
    assert dest == 0
    assert product == 0x02
    assert new_inv[0] == (0x02, 15)
    assert new_inv[1] == (0x0B, 15)


def test_shotgun_reload_from_shells() -> None:
    inv = _inv((0x03, 2), (0x0C, 10))
    planned = plan_combine(inv, 0, 1)
    assert planned is not None
    new_inv, dest, product = planned
    assert dest == 0
    assert product == 0x03
    assert new_inv[0] == (0x03, 7)
    assert new_inv[1] == (0x0C, 5)


def test_reload_works_ammo_first_pick() -> None:
    inv = _inv((0x0C, 5), (0x03, 0))
    planned = plan_combine(inv, 0, 1)
    assert planned is not None
    new_inv, _, _ = planned
    assert new_inv[1] == (0x03, 5)
    assert new_inv[0] == (0, 0)


def test_attack_masked_without_ammo() -> None:
    inv = _inv((0x02, 0), (0, 0))
    m = action_mask(
        N_ACTIONS, None, equipped_weapon_id=0x02, inventory=inv,
        player_anim=0, player_aux=0, player_recovery=0,
    )
    assert not m[ATTACK_ACTION]


def test_attack_legal_with_reserve_ammo() -> None:
    inv = _inv((0x02, 0), (0x0B, 5))
    assert can_fire_weapon(inv, 0x02)
    assert total_fireable_ammo(inv, 0x02) == 5
    m = action_mask(
        N_ACTIONS, None, equipped_weapon_id=0x02, inventory=inv,
        player_anim=0, player_aux=0, player_recovery=0,
    )
    assert m[ATTACK_ACTION]


def test_knife_attack_legal_without_ammo() -> None:
    inv = _inv((0x01, 0), (0, 0))
    m = action_mask(
        N_ACTIONS, None, equipped_weapon_id=0x01, inventory=inv,
        player_anim=0, player_aux=0, player_recovery=0,
    )
    assert m[ATTACK_ACTION]


def test_combine_mask_ammo_merge() -> None:
    inv = _inv((0x0B, 10), (0x0B, 10))
    m = action_mask(N_ACTIONS, None, inventory=inv, combine_phase=0)
    assert m[COMBINE_ACTION]
    m1 = action_mask(N_ACTIONS, None, inventory=inv, combine_phase=1)
    assert m1[SELECT_SLOT_BASE]
    assert m1[SELECT_SLOT_BASE + 1]


def test_combine_mask_beretta_reload() -> None:
    inv = _inv((0x01, 0), (0x02, 0), (0x0B, 30))
    m = action_mask(N_ACTIONS, None, inventory=inv, combine_phase=0)
    assert m[COMBINE_ACTION]
    m1 = action_mask(N_ACTIONS, None, inventory=inv, combine_phase=1)
    assert m1[SELECT_SLOT_BASE + 1]
    assert m1[SELECT_SLOT_BASE + 2]


def test_equip_empty_beretta_with_spare_bullets() -> None:
    inv = _inv((0x01, 0), (0x02, 0), (0x0B, 30))
    m0 = action_mask(
        N_ACTIONS, None,
        equipped_weapon_id=0x01, equipped_slot_0based=0, inventory=inv,
        player_anim=0, player_aux=0, player_recovery=0,
    )
    assert m0[EQUIP_ACTION]
    m1 = action_mask(
        N_ACTIONS, None,
        equipped_weapon_id=0x01, equipped_slot_0based=0, inventory=inv,
        equip_phase=1,
        player_anim=0, player_aux=0, player_recovery=0,
    )
    assert m1[SELECT_SLOT_BASE + 1]


class _FakeBridge:
    def __init__(self, inv: list[tuple[int, int]]) -> None:
        from re1_rl.memory_map import INVENTORY_BASE

        self._base = INVENTORY_BASE
        self._inv = list(inv)
        self.writes: list[list] = []

    def read_block(self, address: int, count: int) -> list[int]:
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


def test_apply_combine_ammo_merge() -> None:
    inv = _inv((0x0B, 10), (0x0B, 5))
    bridge = _FakeBridge(inv)
    result = apply_combine(
        bridge, 0, 1, equipped_weapon_id=0, equipped_slot_0based=None,
    )
    assert result["ok"]
    assert bridge._inv[0] == (0x0B, 15)
    assert bridge._inv[1] == (0, 0)


def test_total_fireable_shotgun_counts_weapon_and_shells() -> None:
    inv = _inv((0x03, 3), (0x0C, 4))
    assert total_fireable_ammo(inv, 0x03) == 7
