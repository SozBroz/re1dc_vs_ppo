"""FIFO reassignment of delayed combat pay onto the fire step."""

from __future__ import annotations

import numpy as np

from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION
from re1_rl.pending_combat_reattribute import (
    combat_pay_from_breakdown,
    is_armed_attack,
    reattribute_pending_combat,
)


def test_combat_pay_sums_only_positive_hit_channels() -> None:
    assert combat_pay_from_breakdown(
        {"enemy_damage": 0.4158, "enemy_kill": 2.0, "ammo_spend": -0.04}
    ) == 2.4158
    assert combat_pay_from_breakdown({"ammo_spend": -0.04}) == 0.0
    assert combat_pay_from_breakdown(None) == 0.0


def test_is_armed_attack_requires_spent_round_or_knife() -> None:
    assert is_armed_attack(
        ATTACK_ACTION,
        {"attack_report": {"outcome": "no_damage", "ammo_spent": 1, "weapon": "beretta"}},
    )
    assert is_armed_attack(
        ATTACK_DOWN_ACTION,
        {"attack_report": {"outcome": "ok", "ammo_spent": 0, "weapon": "knife"}},
    )
    assert not is_armed_attack(
        ATTACK_ACTION,
        {"attack_report": {"outcome": "aim_timeout", "ammo_spent": 0, "weapon": "beretta"}},
    )
    assert not is_armed_attack(1, {"attack_report": {"outcome": "ok", "ammo_spent": 1}})


def test_reattribute_moves_kill_onto_oldest_fire() -> None:
    rewards = np.zeros(8, dtype=np.float32)
    queue = [2]
    rewards[2] = -0.041
    rewards[3] = 0.0
    current, moved, target = reattribute_pending_combat(
        fire_queue=queue,
        current_index=4,
        reward=2.09216,
        rewards=rewards,
        credited_from_pending=True,
        combat_pay=2.09216,
    )
    assert target == 2
    assert moved == 2.09216
    assert current == 0.0
    assert queue == []
    np.testing.assert_allclose(rewards[2], -0.041 + 2.09216, rtol=1e-6)


def test_reattribute_fifo_two_fires() -> None:
    rewards = np.array([-0.04, -0.04, 0.0, 0.0], dtype=np.float32)
    queue = [0, 1]
    current, moved, target = reattribute_pending_combat(
        fire_queue=queue,
        current_index=2,
        reward=0.4158,
        rewards=rewards,
        credited_from_pending=True,
        combat_pay=0.4158,
    )
    assert target == 0
    assert moved == 0.4158
    assert current == 0.0
    assert queue == [1]
    current2, moved2, target2 = reattribute_pending_combat(
        fire_queue=queue,
        current_index=3,
        reward=0.4158,
        rewards=rewards,
        credited_from_pending=True,
        combat_pay=0.4158,
    )
    assert target2 == 1
    assert moved2 == 0.4158
    assert current2 == 0.0
    assert queue == []
    np.testing.assert_allclose(rewards[0], 0.3758, rtol=1e-5)
    np.testing.assert_allclose(rewards[1], 0.3758, rtol=1e-5)


def test_reattribute_leaves_pay_if_no_queued_fire() -> None:
    rewards = np.zeros(2, dtype=np.float32)
    queue: list[int] = []
    current, moved, target = reattribute_pending_combat(
        fire_queue=queue,
        current_index=1,
        reward=2.09,
        rewards=rewards,
        credited_from_pending=True,
        combat_pay=2.09,
    )
    assert current == 2.09
    assert moved == 0.0
    assert target is None
