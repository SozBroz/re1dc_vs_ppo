"""Mask and reward parity across attack / attack_up / attack_down."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import (
    ATTACK_ACTION,
    ATTACK_DOWN_ACTION,
    ATTACK_UP_ACTION,
    action_mask,
)
from re1_rl.enemy_combat import apply_combat_step_fields
from re1_rl.env import ACTION_NAMES
from re1_rl.progress import ProgressTracker
from re1_rl.reward import compute_reward
from tests.test_scaffolding import make_planner, make_state

N_ACTIONS = len(ACTION_NAMES)
HEIGHT_ACTIONS = (ATTACK_UP_ACTION, ATTACK_ACTION, ATTACK_DOWN_ACTION)


def test_all_height_masks_identical() -> None:
    cases = [
        {"equipped_weapon_id": 0x01, "knife_enemies_near": 1},
        {"equipped_weapon_id": 0x02, "inventory": [(0x02, 5)] + [(0, 0)] * 7, "gun_enemies_near": 1},
        {"equipped_weapon_id": 0x02, "inventory": [(0x02, 0)] + [(0, 0)] * 7, "gun_enemies_near": 1},
        {"equipped_weapon_id": 0x01, "knife_enemies_near": 0},
        {"player_anim": 0x13, "player_aux": 0x04, "player_recovery": 8, "equipped_weapon_id": 0x01},
    ]
    for overrides in cases:
        kwargs = {
            "player_anim": 0,
            "player_aux": 0,
            "player_recovery": 0,
            **overrides,
        }
        mask = action_mask(N_ACTIONS, None, **kwargs)
        base = mask[ATTACK_ACTION]
        for idx in HEIGHT_ACTIONS:
            assert mask[idx] == base, (overrides, idx)


def test_knife_miss_reward_same_for_all_heights() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    cur["equipped_weapon_id"] = 0x01
    prev["enemies"] = [{"slot": 0, "hp": 80, "in_room": 1}]
    cur["enemies"] = [{"slot": 0, "hp": 80, "in_room": 1}]

    bd_by_height: dict[int, dict] = {}
    for action_id in HEIGHT_ACTIONS:
        out = apply_combat_step_fields(prev, cur, knife=True, attack=True)
        _, bd = compute_reward(
            prev, out, planner, progress=ProgressTracker(), return_breakdown=True,
        )
        bd_by_height[action_id] = bd

    first = bd_by_height[ATTACK_ACTION]
    for action_id in (ATTACK_UP_ACTION, ATTACK_DOWN_ACTION):
        assert bd_by_height[action_id] == first, action_id


def test_gun_hit_reward_same_for_all_heights() -> None:
    planner = make_planner()
    prev = make_state(hp=96, step=1)
    cur = make_state(hp=96, step=2)
    prev["enemies"] = [{"slot": 0, "hp": 80, "in_room": 1, "type_id": 1}]
    cur["enemies"] = [{"slot": 0, "hp": 60, "in_room": 1, "type_id": 1}]
    cur["enemy_damage"] = 20
    cur["enemy_kills"] = 0

    bd_by_height: dict[int, dict] = {}
    for action_id in HEIGHT_ACTIONS:
        _, bd = compute_reward(
            prev, cur, planner, progress=ProgressTracker(), return_breakdown=True,
        )
        bd_by_height[action_id] = bd

    first = bd_by_height[ATTACK_ACTION]
    for action_id in (ATTACK_UP_ACTION, ATTACK_DOWN_ACTION):
        assert bd_by_height[action_id]["enemy_damage"] == first["enemy_damage"]
        assert bd_by_height[action_id]["attack_miss"] == first["attack_miss"]
