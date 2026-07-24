"""Attack-miss flags attach to state; ammo waste taxes missed gun rounds."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    AMMO_PICKUP_BONUS,
    ENEMY_DAMAGE_REWARD,
    ENEMY_KILL_REWARD,
    ITEM_PICKUP_BONUS,
    KNIFE_MISS_PENALTY,
    MISS_TAX_CLIP_SIZE,
    REFERENCE_STEP_FRAMES,
    STEP_PENALTY,
    ammo_waste_per_missed_round,
    ammo_waste_penalty,
    compute_reward,
)
from tests.test_scaffolding import make_planner, make_state


def test_miss_tax_formula_handgun_and_shotgun() -> None:
    assert ammo_waste_per_missed_round(0x02) == pytest.approx(
        -AMMO_PICKUP_BONUS / 15
    )
    assert ammo_waste_per_missed_round(0x03) == pytest.approx(
        -AMMO_PICKUP_BONUS / 7
    )
    assert ammo_waste_per_missed_round(0x02) == pytest.approx(-2.0 / 15)
    assert ammo_waste_per_missed_round(0x05) == pytest.approx(-2.0 / 6)
    assert ammo_waste_per_missed_round(0x01) == 0.0  # knife
    assert ammo_waste_per_missed_round(0x06) == 0.0  # flamethrower


def test_miss_tax_clip_table_matches_validated_sizes() -> None:
    assert MISS_TAX_CLIP_SIZE[0x02] == 15
    assert MISS_TAX_CLIP_SIZE[0x03] == 7
    assert MISS_TAX_CLIP_SIZE[0x04] == 6
    assert MISS_TAX_CLIP_SIZE[0x05] == 6
    assert MISS_TAX_CLIP_SIZE[0x07] == 6
    assert MISS_TAX_CLIP_SIZE[0x08] == 6
    assert MISS_TAX_CLIP_SIZE[0x09] == 6
    assert MISS_TAX_CLIP_SIZE[0x0A] == 6


def test_attack_missed_taxes_ammo_by_clip() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["attack_missed"] = True
    cur["ammo_spent"] = 1
    cur["equipped_weapon_id"] = 0x02  # beretta
    cur["step_emulated_frames"] = 42
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["attack_miss"] == 0.0
    assert bd["ammo_waste"] == pytest.approx(-2.0 / 15)
    assert bd["step"] == STEP_PENALTY * (42 / REFERENCE_STEP_FRAMES)


def test_knife_swing_missed_penalty_no_ammo_tax() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["knife_swing_missed"] = True
    cur["equipped_weapon_id"] = 0x01
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["attack_miss"] == pytest.approx(KNIFE_MISS_PENALTY)
    assert bd["ammo_waste"] == 0.0


def test_ammo_spent_on_miss_scales_waste() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["attack_missed"] = True
    cur["ammo_spent"] = 3
    cur["equipped_weapon_id"] = 0x02
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["ammo_waste"] == pytest.approx(ammo_waste_penalty(0x02, 3))
    assert bd["ammo_waste"] == pytest.approx(-6.0 / 15)


def test_shotgun_miss_uses_seven_round_clip() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["attack_missed"] = True
    cur["ammo_spent"] = 1
    cur["equipped_weapon_id"] = 0x03
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["ammo_waste"] == pytest.approx(-AMMO_PICKUP_BONUS / 7)


def test_no_ammo_waste_without_miss_flag() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["ammo_spent"] = 2
    cur["equipped_weapon_id"] = 0x02
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["attack_miss"] == 0.0
    assert bd["ammo_waste"] == 0.0


def test_hit_rewards_unchanged_no_miss_tax() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["enemy_damage"] = 20
    cur["ammo_spent"] = 1
    cur["equipped_weapon_id"] = 0x02
    reward, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["enemy_damage"] == ENEMY_DAMAGE_REWARD * 20
    assert bd["attack_miss"] == 0.0
    assert bd["ammo_waste"] == 0.0
    assert reward == sum(bd.values())


def test_enemy_kill_reward_is_static() -> None:
    assert ENEMY_KILL_REWARD == pytest.approx(0.24)
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["enemy_kills"] = 1
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["enemy_kill"] == pytest.approx(ENEMY_KILL_REWARD)


def test_breakdown_keys_present() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert "attack_miss" in bd
    assert "ammo_waste" in bd
