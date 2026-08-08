"""Equip thrash diagnostics in step_diag memlog rows."""

from __future__ import annotations

import numpy as np

from re1_rl.action_mask import EQUIP_ACTION, N_SELECT_SLOT, SELECT_SLOT_BASE, action_mask
from re1_rl.env import ACTION_NAMES
from re1_rl.step_diag import (
    _equip_policy_probs,
    _inventory_weapon_slots,
    _legal_equip_select_slots,
)

N_ACTIONS = len(ACTION_NAMES)


def test_equip_policy_probs_exposes_legal_select_mass() -> None:
    inv = [("knife", 1), ("beretta", 15), ("shield_key", 1), ("shotgun", 5)]
    mask = action_mask(
        N_ACTIONS,
        None,
        player_anim=0x0D,
        player_aux=0x01,
        player_recovery=0,
        equipped_weapon_id=0x03,
        equipped_slot_0based=3,
        inventory=[(1, 1), (2, 15), (0x17, 1), (3, 5)],
        equip_phase=1,
    )
    probs = np.zeros(N_ACTIONS, dtype=np.float64)
    # Mass on knife (0) and beretta (1); shotgun illegal while equipped.
    probs[SELECT_SLOT_BASE + 0] = 0.7
    probs[SELECT_SLOT_BASE + 1] = 0.3
    snip = _equip_policy_probs(probs, mask)
    assert snip is not None
    assert snip["p_select"]["0"] == 0.7
    assert snip["p_select"]["1"] == 0.3
    assert "3" not in snip["p_select"]
    legal = _legal_equip_select_slots(mask, inv)
    assert {row["s"] for row in legal} == {0, 1}
    assert {row["n"] for row in legal} == {"knife", "beretta"}


def test_inventory_weapon_slots_keeps_indices() -> None:
    inv = [
        ("knife", 1),
        ("beretta", 15),
        ("first_aid_spray_alt", 1),
        ("shield_key", 1),
        ("handgun_bullets", 10),
        ("green_herb", 1),
        ("shotgun", 5),
        ("", 0),
    ]
    weapons = _inventory_weapon_slots(inv)
    assert weapons == [
        {"s": 0, "n": "knife"},
        {"s": 1, "n": "beretta"},
        {"s": 6, "n": "shotgun"},
    ]


def test_equip_open_legal_false_under_cooldown() -> None:
    mask = action_mask(
        N_ACTIONS,
        None,
        player_anim=0x0D,
        player_aux=0x01,
        player_recovery=0,
        equipped_weapon_id=0x01,
        inventory=[(1, 1), (2, 15), (3, 5)],
        equip_switch_cooldown=5,
    )
    probs = np.zeros(N_ACTIONS, dtype=np.float64)
    probs[EQUIP_ACTION] = 0.4
    snip = _equip_policy_probs(probs, mask)
    assert snip is not None
    assert snip["open_legal"] is False
    assert snip["p_equip"] == 0.4
