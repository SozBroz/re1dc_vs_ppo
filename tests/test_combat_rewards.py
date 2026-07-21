"""Combat reward terms (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.progress import ProgressTracker
from re1_rl.reward import (
    ENEMY_DAMAGE_REWARD,
    ENEMY_KILL_REWARD,
    REFERENCE_STEP_FRAMES,
    STEP_PENALTY,
    compute_reward,
)
from tests.test_scaffolding import make_planner, make_state


def test_step_penalty_constant() -> None:
    assert STEP_PENALTY == pytest.approx(-0.00024)
    assert ENEMY_DAMAGE_REWARD == pytest.approx(0.006)
    assert ENEMY_KILL_REWARD == pytest.approx(0.24)


def test_enemy_damage_and_kill_rewards() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["enemy_damage"] = 40
    cur["enemy_kills"] = 1
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["enemy_damage"] == ENEMY_DAMAGE_REWARD * 40
    assert bd["enemy_kill"] == ENEMY_KILL_REWARD


def test_knife_miss_only_scaled_step_contempt() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["knife_swing_missed"] = True
    cur["step_emulated_frames"] = 42
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["attack_miss"] == 0.0
    assert bd["step"] == STEP_PENALTY * (42 / REFERENCE_STEP_FRAMES)


def test_submenu_ack_uses_reference_step_contempt() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["step_emulated_frames"] = REFERENCE_STEP_FRAMES
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["step"] == STEP_PENALTY


def test_inventory_macro_scales_step_contempt() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    macro_frames = 180
    cur["step_emulated_frames"] = macro_frames
    _, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["step"] == STEP_PENALTY * (macro_frames / REFERENCE_STEP_FRAMES)


def test_knife_hit_rewards_stack_on_scaled_step_contempt() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["step_emulated_frames"] = 40
    cur["enemy_damage"] = 20
    cur["enemy_kills"] = 1
    reward, bd = compute_reward(
        prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
    )
    assert bd["step"] == STEP_PENALTY * (40 / REFERENCE_STEP_FRAMES)
    assert bd["enemy_damage"] == ENEMY_DAMAGE_REWARD * 20
    assert bd["enemy_kill"] == ENEMY_KILL_REWARD
    assert bd["attack_miss"] == 0.0
    assert bd["ammo_waste"] == 0.0
    assert reward == sum(bd.values())
