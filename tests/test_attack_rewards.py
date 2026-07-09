"""Attack-miss and ammo-waste reward terms (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    AMMO_WASTE_PENALTY,
    ATTACK_MISS_PENALTY,
    ENEMY_DAMAGE_REWARD,
    KNIFE_MISS_PENALTY,
    REFERENCE_STEP_FRAMES,
    STEP_PENALTY,
    compute_reward,
)
from tests.test_scaffolding import make_planner, make_state


def test_attack_missed_triggers_attack_miss_penalty() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["attack_missed"] = True
    cur["step_emulated_frames"] = 42
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["attack_miss"] == ATTACK_MISS_PENALTY
    assert bd["attack_miss"] == STEP_PENALTY
    assert bd["ammo_waste"] == 0.0
    assert bd["step"] == STEP_PENALTY * (42 / REFERENCE_STEP_FRAMES)


def test_knife_swing_missed_back_compat() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["knife_swing_missed"] = True
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["attack_miss"] == KNIFE_MISS_PENALTY
    assert bd["ammo_waste"] == 0.0


def test_ammo_waste_on_ranged_miss() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["attack_missed"] = True
    cur["ammo_spent"] = 3
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["attack_miss"] == ATTACK_MISS_PENALTY
    assert bd["ammo_waste"] == AMMO_WASTE_PENALTY * 3


def test_ammo_waste_capped_at_four_rounds() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["attack_missed"] = True
    cur["ammo_spent"] = 9
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["ammo_waste"] == AMMO_WASTE_PENALTY * 4


def test_no_ammo_waste_without_miss() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["ammo_spent"] = 2
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["attack_miss"] == 0.0
    assert bd["ammo_waste"] == 0.0


def test_hit_no_attack_miss_or_ammo_waste() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["enemy_damage"] = 20
    cur["ammo_spent"] = 1
    reward, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["enemy_damage"] == ENEMY_DAMAGE_REWARD * 20
    assert bd["attack_miss"] == 0.0
    assert bd["ammo_waste"] == 0.0
    assert reward == sum(bd.values())


def test_breakdown_keys_present() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert "attack_miss" in bd
    assert "ammo_waste" in bd
