"""Enemy HP delta helpers (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.enemy_combat import (
    alive_enemy_count,
    apply_combat_step_fields,
    combat_enemy_count,
    enemy_combat_delta,
    enemy_hp_by_slot,
)


def test_alive_enemy_count() -> None:
    enemies = [
        {"slot": 0, "hp": 80, "alive": True},
        {"slot": 1, "hp": 0, "alive": True},
        {"slot": 2, "hp": 10, "alive": False},
    ]
    assert alive_enemy_count(enemies) == 1
    assert alive_enemy_count([]) == 0
    assert alive_enemy_count(None) == 0


def test_combat_enemy_count() -> None:
    enemies = [
        {"slot": 0, "hp": 80, "combat_near": 1},
        {"slot": 1, "hp": 50, "combat_near": 0},
        {"slot": 2, "hp": 0, "combat_near": 1},
    ]
    assert combat_enemy_count(enemies) == 1
    assert combat_enemy_count([]) == 0


def test_enemy_hp_by_slot_skips_dead() -> None:
    enemies = [{"slot": 0, "hp": 80}, {"slot": 2, "hp": 0}]
    assert enemy_hp_by_slot(enemies) == {0: 80}


def test_damage_and_kill_delta() -> None:
    prev = {0: 100, 1: 50}
    curr = {0: 60, 1: 0}
    damage, kills = enemy_combat_delta(prev, curr)
    assert damage == 90
    assert kills == 1


def test_new_spawn_not_counted_as_kill() -> None:
    prev = {}
    curr = {0: 100}
    damage, kills = enemy_combat_delta(prev, curr)
    assert damage == 0
    assert kills == 0


def test_apply_combat_step_fields_miss() -> None:
    prev = {"enemies": [{"slot": 0, "hp": 96}]}
    cur = {"enemies": [{"slot": 0, "hp": 96}]}
    out = apply_combat_step_fields(prev, cur, attack=True)
    assert out["enemy_damage"] == 0
    assert out["enemy_kills"] == 0
    assert out["combat_events"] == []
    assert out["attack_missed"] is True


def test_apply_combat_step_fields_chip() -> None:
    prev = {"enemies": [{"slot": 0, "hp": 40}]}
    cur = {"enemies": [{"slot": 0, "hp": 20}]}
    out = apply_combat_step_fields(prev, cur, knife=True)
    assert out["enemy_damage"] == 20
    assert out["enemy_kills"] == 0
    assert len(out["combat_events"]) == 1
    assert out["combat_events"][0]["damage"] == 20


def test_apply_combat_step_fields_kill() -> None:
    prev = {"enemies": [{"slot": 0, "hp": 40}]}
    cur = {"enemies": []}
    out = apply_combat_step_fields(prev, cur, attack=True)
    assert out["enemy_damage"] == 40
    assert out["enemy_kills"] == 1
    assert "attack_missed" not in out
