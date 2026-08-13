"""Attack-miss flags attach to state; ammo spend + waste tax gun rounds."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    AMMO_SPEND_TAX_PER_ROUND,
    AMMO_WASTE_MAX_PENALTY,
    ATTACK_MISS_TAX_SCALE,
    ENEMY_DAMAGE_REWARD,
    ENEMY_KILL_REWARD,
    HEAVY_WEAPON_FODDER_HIT_PENALTY,
    BOSS_COMBAT_REWARD_SCALE,
    KNIFE_MISS_PENALTY,
    MISS_TAX_CLIP_SIZE,
    REFERENCE_STEP_FRAMES,
    STEP_PENALTY,
    ammo_spend_penalty,
    ammo_spend_per_round,
    ammo_waste_per_missed_round,
    ammo_waste_penalty,
    combat_overkill_penalty,
    compute_reward,
    heavy_weapon_fodder_hit_penalty,
    shotgun_dog_hit_penalty,
    SHOTGUN_DOG_HIT_PENALTY,
)
from tests.test_scaffolding import make_planner, make_state


def test_miss_tax_formula_handgun_and_shotgun() -> None:
    assert ATTACK_MISS_TAX_SCALE == pytest.approx(0.20)
    assert KNIFE_MISS_PENALTY == pytest.approx(-0.002)
    assert ammo_waste_per_missed_round(0x02) == pytest.approx(-2.0 / 15.0 * 0.20)
    assert ammo_waste_per_missed_round(0x03) == pytest.approx(-2.0 / 7.0 * 0.20)
    assert ammo_waste_per_missed_round(0x05) == pytest.approx(-2.0 / 6.0 * 0.20)
    assert ammo_waste_per_missed_round(0x01) == 0.0  # knife
    assert ammo_waste_per_missed_round(0x06) == 0.0  # flamethrower


def test_ammo_spend_tax_tiers() -> None:
    assert AMMO_SPEND_TAX_PER_ROUND[0x02] == pytest.approx(0.04)
    assert AMMO_SPEND_TAX_PER_ROUND[0x03] == pytest.approx(0.25)
    assert AMMO_SPEND_TAX_PER_ROUND[0x05] == pytest.approx(0.40)
    assert AMMO_SPEND_TAX_PER_ROUND[0x0A] == pytest.approx(0.75)
    assert ammo_spend_per_round(0x01) == 0.0
    assert ammo_spend_per_round(0x06) == 0.0
    assert ammo_spend_penalty(0x03, 2) == pytest.approx(-0.50)


def test_miss_tax_clip_table_matches_validated_sizes() -> None:
    assert MISS_TAX_CLIP_SIZE[0x02] == 15
    assert MISS_TAX_CLIP_SIZE[0x03] == 7
    assert MISS_TAX_CLIP_SIZE[0x04] == 6
    assert MISS_TAX_CLIP_SIZE[0x05] == 6
    assert MISS_TAX_CLIP_SIZE[0x07] == 6
    assert MISS_TAX_CLIP_SIZE[0x08] == 6
    assert MISS_TAX_CLIP_SIZE[0x09] == 6
    assert MISS_TAX_CLIP_SIZE[0x0A] == 6


def test_scaled_miss_tax_ramps_below_two_clips() -> None:
    base = ammo_waste_per_missed_round(0x02)
    full = ammo_waste_per_missed_round(0x02, ammo_before=30)
    one_mag = ammo_waste_per_missed_round(0x02, ammo_before=15)
    last = ammo_waste_per_missed_round(0x02, ammo_before=1)
    assert full == pytest.approx(base)
    assert one_mag == pytest.approx(-0.142, abs=0.001)
    assert last == pytest.approx(-AMMO_WASTE_MAX_PENALTY)


def test_scaled_miss_uses_inventory_before_waste() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(
        hp=96,
        step=2,
        inventory_slots=[("beretta", 14), ("handgun_bullets", 0)],
    )
    cur["attack_missed"] = True
    cur["ammo_spent"] = 1
    cur["equipped_weapon_id"] = 0x02
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["ammo_waste"] == pytest.approx(
        ammo_waste_penalty(0x02, 1, ammo_before=15)
    )


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
    assert bd["ammo_spend"] == pytest.approx(ammo_spend_penalty(0x02, 1))
    assert bd["ammo_waste"] == pytest.approx(ammo_waste_per_missed_round(0x02))
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
    assert bd["ammo_spend"] == 0.0
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
    assert bd["ammo_spend"] == pytest.approx(ammo_spend_penalty(0x02, 3))
    assert bd["ammo_waste"] == pytest.approx(
        3 * ammo_waste_per_missed_round(0x02)
    )


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
    assert bd["ammo_spend"] == pytest.approx(ammo_spend_penalty(0x03, 1))
    assert bd["ammo_waste"] == pytest.approx(ammo_waste_per_missed_round(0x03))


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
    assert bd["ammo_spend"] == pytest.approx(ammo_spend_penalty(0x02, 2))


def test_hit_pays_ammo_spend_not_miss_waste() -> None:
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
    assert bd["ammo_spend"] == pytest.approx(ammo_spend_penalty(0x02, 1))
    assert reward == sum(bd.values())


def test_shotgun_hit_spend_still_leaves_kill_positive() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["enemy_damage"] = 20
    cur["enemy_kills"] = 1
    cur["ammo_spent"] = 1
    cur["equipped_weapon_id"] = 0x03
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["ammo_spend"] == pytest.approx(-0.25)
    combat_net = bd["enemy_damage"] + bd["enemy_kill"] + bd["ammo_spend"]
    assert combat_net == pytest.approx(ENEMY_DAMAGE_REWARD * 20 + ENEMY_KILL_REWARD - 0.25)
    assert combat_net > 1.5


def test_deferred_miss_expiry_skips_duplicate_spend() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["attack_missed"] = True
    cur["pending_combat_expired"] = True
    cur["ammo_spent"] = 1
    cur["pending_miss_weapon_id"] = 0x03
    cur["equipped_weapon_id"] = 0x03
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["ammo_spend"] == 0.0
    assert bd["ammo_waste"] == pytest.approx(ammo_waste_per_missed_round(0x03))


def test_deferred_gl_misses_pay_waste_without_duplicate_spend() -> None:
    """Two GL rounds expiring on later steps pay ammo_waste, not ammo_spend."""
    planner = make_planner()
    prev = make_state(hp=96, step=10)
    cur = make_state(hp=96, step=11)
    cur["attack_missed"] = True
    cur["pending_combat_expired"] = True
    cur["ammo_spent"] = 1
    cur["pending_miss_weapon_id"] = 0x07
    cur["equipped_weapon_id"] = 0x07
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["ammo_spend"] == 0.0
    assert bd["ammo_waste"] == pytest.approx(ammo_waste_per_missed_round(0x07))

    prev2 = make_state(hp=96, step=11)
    cur2 = make_state(hp=96, step=12)
    cur2["attack_missed"] = True
    cur2["pending_combat_expired"] = True
    cur2["ammo_spent"] = 1
    cur2["pending_miss_weapon_id"] = 0x07
    cur2["equipped_weapon_id"] = 0x07
    _, bd2 = compute_reward(
        prev2, cur2, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd2["ammo_spend"] == 0.0
    assert bd2["ammo_waste"] == pytest.approx(ammo_waste_per_missed_round(0x07))


def test_enemy_kill_reward_is_static() -> None:
    assert ENEMY_KILL_REWARD == pytest.approx(2.0)
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
    assert "ammo_spend" in bd
    assert "ammo_waste" in bd
    assert "combat_overkill" in bd
    assert "shotgun_dog_hit" in bd
    assert "heavy_weapon_fodder_hit" in bd


def test_shotgun_dog_hit_penalty_per_event() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x03
    cur["ammo_spent"] = 1
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 40,
            "killed": False,
            "reward_denied": False,
            "is_cerberus": True,
            "type_id": 0x0F,
            "active_byte": 0x90,
        }
    ]
    assert shotgun_dog_hit_penalty(cur) == pytest.approx(SHOTGUN_DOG_HIT_PENALTY)
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["shotgun_dog_hit"] == pytest.approx(SHOTGUN_DOG_HIT_PENALTY)
    assert bd["enemy_damage"] == pytest.approx(40 * ENEMY_DAMAGE_REWARD)


def test_shotgun_zombie_hit_no_dog_penalty() -> None:
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x03
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 20,
            "killed": False,
            "reward_denied": False,
            "is_cerberus": False,
            "type_id": 0x0F,
            "active_byte": 0,
        }
    ]
    assert shotgun_dog_hit_penalty(cur) == 0.0


def test_magnum_zombie_hit_pays_heavy_fodder_penalty() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x05
    cur["ammo_spent"] = 1
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 100,
            "killed": True,
            "reward_denied": False,
            "is_cerberus": False,
            "is_zombie": True,
            "type_id": 1,
        }
    ]
    assert heavy_weapon_fodder_hit_penalty(cur) == pytest.approx(
        HEAVY_WEAPON_FODDER_HIT_PENALTY
    )
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["heavy_weapon_fodder_hit"] == pytest.approx(
        HEAVY_WEAPON_FODDER_HIT_PENALTY
    )


def test_bazooka_dog_hit_pays_heavy_fodder_penalty() -> None:
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x07
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 60,
            "killed": False,
            "reward_denied": False,
            "is_cerberus": True,
            "is_zombie": False,
        }
    ]
    assert heavy_weapon_fodder_hit_penalty(cur) == pytest.approx(
        HEAVY_WEAPON_FODDER_HIT_PENALTY
    )


def test_bazooka_kind0f_zombie_without_dog_byte_pays_heavy_fodder_penalty() -> None:
    """Dining 2F zombies often expose type_id 0x0F without cerberus active_byte."""
    planner = make_planner()
    prev = make_state(hp=96, room="202", step=1)
    cur = make_state(hp=96, room="202", step=2)
    cur["equipped_weapon_id"] = 0x07
    cur["ammo_spent"] = 1
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 25,
            "killed": True,
            "reward_denied": False,
            "is_cerberus": False,
            "is_zombie": True,
            "type_id": 0x0F,
        }
    ]
    assert heavy_weapon_fodder_hit_penalty(cur) == pytest.approx(
        HEAVY_WEAPON_FODDER_HIT_PENALTY
    )
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["heavy_weapon_fodder_hit"] == pytest.approx(
        HEAVY_WEAPON_FODDER_HIT_PENALTY
    )


def test_beretta_zombie_hit_no_heavy_fodder_penalty() -> None:
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x02
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 12,
            "killed": False,
            "reward_denied": False,
            "is_cerberus": False,
            "is_zombie": True,
        }
    ]
    assert heavy_weapon_fodder_hit_penalty(cur) == 0.0


def test_bazooka_yawn_hit_no_heavy_fodder_penalty() -> None:
    """Room 210 Yawn is kind 0x0F with logical HP 120 — not dining fodder."""
    planner = make_planner()
    prev = make_state(hp=96, room="210", step=1)
    cur = make_state(hp=96, room="210", step=2)
    cur["equipped_weapon_id"] = 0x07
    cur["ammo_spent"] = 1
    prev["enemies"] = [
        {"slot": 0, "hp": 120, "type_id": 0x0F, "active_byte": 15}
    ]
    cur["enemies"] = [
        {"slot": 0, "hp": 105, "type_id": 0x0F, "active_byte": 15}
    ]
    from re1_rl.enemy_combat import apply_combat_step_fields

    cur = apply_combat_step_fields(
        prev, cur, knife=False, attack=True, credit_damage=True
    )
    assert cur["combat_events"][0]["is_yawn"] is True
    assert cur["combat_events"][0]["is_boss"] is True
    assert cur["combat_events"][0]["is_zombie"] is False
    assert heavy_weapon_fodder_hit_penalty(cur) == 0.0
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["heavy_weapon_fodder_hit"] == 0.0
    assert bd["enemy_damage"] == pytest.approx(
        15 * ENEMY_DAMAGE_REWARD * BOSS_COMBAT_REWARD_SCALE
    )


def test_boss_hits_pay_four_times_fodder_stays_one_x() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    boss = make_state(hp=96, step=2)
    boss["combat_events"] = [
        {
            "slot": 0,
            "damage": 10,
            "killed": False,
            "reward_denied": False,
            "is_boss": True,
            "is_yawn": False,
        }
    ]
    fodder = make_state(hp=96, step=2)
    fodder["combat_events"] = [
        {
            "slot": 0,
            "damage": 10,
            "killed": False,
            "reward_denied": False,
            "is_boss": False,
            "is_zombie": True,
        }
    ]
    _, boss_bd = compute_reward(
        prev, boss, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    _, fodder_bd = compute_reward(
        prev, fodder, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert boss_bd["enemy_damage"] == pytest.approx(
        10 * ENEMY_DAMAGE_REWARD * BOSS_COMBAT_REWARD_SCALE
    )
    assert fodder_bd["enemy_damage"] == pytest.approx(10 * ENEMY_DAMAGE_REWARD)
    kill = make_state(hp=96, step=2)
    kill["combat_events"] = [
        {
            "slot": 0,
            "damage": 40,
            "killed": True,
            "reward_denied": False,
            "is_boss": True,
        }
    ]
    _, kill_bd = compute_reward(
        prev, kill, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert kill_bd["enemy_kill"] == pytest.approx(
        ENEMY_KILL_REWARD * BOSS_COMBAT_REWARD_SCALE
    )


def test_beretta_dog_hit_no_shotgun_penalty() -> None:
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x02
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 12,
            "killed": False,
            "reward_denied": False,
            "is_cerberus": True,
        }
    ]
    assert shotgun_dog_hit_penalty(cur) == 0.0


def test_combat_overkill_beretta_one_hp_kill() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x02
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 1,
            "killed": True,
            "reward_denied": False,
            "is_crow": False,
        }
    ]
    per_round = ammo_waste_per_missed_round(0x02)
    expected = (3 / 4.0) * per_round
    assert combat_overkill_penalty(cur) == pytest.approx(expected)
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["combat_overkill"] == pytest.approx(expected)
    assert bd["enemy_kill"] == ENEMY_KILL_REWARD


def test_combat_overkill_scales_with_low_ammo() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(
        hp=96,
        step=2,
        inventory_slots=[("beretta", 1), ("handgun_bullets", 0)],
    )
    cur["equipped_weapon_id"] = 0x02
    cur["ammo_spent"] = 1
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 1,
            "killed": True,
            "reward_denied": False,
            "is_crow": False,
        }
    ]
    from re1_rl.ammo_accounting import fireable_ammo_before_miss

    per_round = ammo_waste_per_missed_round(
        0x02,
        ammo_before=fireable_ammo_before_miss(cur, 0x02, rounds_spent=1),
    )
    expected = (3 / 4.0) * per_round
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["combat_overkill"] == pytest.approx(expected)
    assert abs(bd["combat_overkill"]) > abs((3 / 4.0) * ammo_waste_per_missed_round(0x02))


def test_combat_overkill_no_penalty_on_efficient_kill() -> None:
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x02
    cur["combat_events"] = [
        {
            "slot": 0,
            "damage": 4,
            "killed": True,
            "reward_denied": False,
            "is_crow": False,
        }
    ]
    assert combat_overkill_penalty(cur) == 0.0


def test_yawn_rails_keeps_combat_hit_positive_unscaled_against_miss_tax() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    hit = make_state(hp=96, step=2)
    hit["enemy_damage"] = 4
    hit["ammo_spent"] = 1
    hit["equipped_weapon_id"] = 0x02
    _, hit_bd = compute_reward(
        prev, hit, planner, progress=ProgressTracker(),
        rails_mode=True, return_breakdown=True,
    )
    miss = make_state(hp=96, step=2)
    miss["attack_missed"] = True
    miss["ammo_spent"] = 1
    miss["equipped_weapon_id"] = 0x02
    _, miss_bd = compute_reward(
        prev, miss, make_planner(), progress=ProgressTracker(),
        rails_mode=True, return_breakdown=True,
    )
    assert hit_bd["enemy_damage"] == pytest.approx(4 * ENEMY_DAMAGE_REWARD)
    assert hit_bd["ammo_spend"] == pytest.approx(ammo_spend_penalty(0x02, 1))
    assert hit_bd["enemy_damage"] + hit_bd["ammo_spend"] > 0.0
    assert abs(miss_bd["ammo_spend"]) + abs(miss_bd["ammo_waste"]) > abs(
        hit_bd["ammo_spend"]
    )