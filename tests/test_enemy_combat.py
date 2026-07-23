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
        {"slot": 0, "hp": 80, "combat_near": 1, "knife_near": 1},
        {"slot": 1, "hp": 50, "combat_near": 0, "knife_near": 0},
        {"slot": 2, "hp": 0, "combat_near": 1, "knife_near": 1},
    ]
    assert combat_enemy_count(enemies) == 1
    assert combat_enemy_count([]) == 0


def test_combat_enemy_count_knife_band() -> None:
    """Knife band is tighter than gun; mid-range enemy arms gun only."""
    enemies = [
        {
            "slot": 0,
            "hp": 80,
            "in_room": 1,
            "combat_near": 1,
            "knife_near": 0,
            "dist": 6500,
        },
        {
            "slot": 1,
            "hp": 50,
            "in_room": 1,
            "combat_near": 1,
            "knife_near": 1,
            "dist": 1200,
        },
    ]
    assert combat_enemy_count(enemies) == 2
    assert combat_enemy_count(enemies, knife=True) == 1
    assert combat_enemy_count(enemies, max_dist=5000) == 1
    assert combat_enemy_count(enemies, max_dist=7000) == 2


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
    prev = {"room_id": "104", "enemies": [{"slot": 0, "hp": 40}]}
    cur = {"room_id": "104", "enemies": []}
    out = apply_combat_step_fields(prev, cur, attack=True)
    assert out["enemy_damage"] == 40
    assert out["enemy_kills"] == 1
    assert "attack_missed" not in out


def test_room_change_does_not_count_unload_as_kill() -> None:
    """Door exit unloads Kenneth/Barry — must not mint combat reward."""
    prev = {
        "room_id": "104",
        "enemies": [{"slot": 0, "hp": 53}],
    }
    cur = {
        "room_id": "105",
        "enemies": [],
    }
    out = apply_combat_step_fields(prev, cur)
    assert out["enemy_damage"] == 0
    assert out["enemy_kills"] == 0
    assert out["combat_events"] == []

    # Re-enter tea room with Kenneth at full HP — still not a kill credit.
    prev2 = {"room_id": "105", "enemies": [{"slot": 0, "hp": 80}]}
    cur2 = {"room_id": "104", "enemies": [{"slot": 0, "hp": 53}]}
    out2 = apply_combat_step_fields(prev2, cur2)
    assert out2["enemy_damage"] == 0
    assert out2["enemy_kills"] == 0


def test_interact_hp_flicker_does_not_pay_damage() -> None:
    """Same-room HP drop without knife/attack (door interact) must not pay."""
    prev = {"room_id": "105", "enemies": [{"slot": 0, "hp": 52}]}
    cur = {"room_id": "105", "enemies": [{"slot": 0, "hp": 40}]}
    out = apply_combat_step_fields(prev, cur)
    assert out["enemy_damage"] == 0
    assert out["enemy_kills"] == 0
    assert out["combat_events"] == []
    # Real hit still pays when attack/knife this step.
    out_hit = apply_combat_step_fields(prev, cur, attack=True)
    assert out_hit["enemy_damage"] == 12
    assert out_hit["enemy_kills"] == 0


def test_wasp_room_408_denies_combat_pay() -> None:
    """Honeycomb wasps respawn — no damage/kill reward in exclusive room 408."""
    prev = {"room_id": "408", "enemies": [{"slot": 0, "hp": 20}]}
    cur = {"room_id": "408", "enemies": [{"slot": 0, "hp": 0}]}
    out = apply_combat_step_fields(prev, cur, attack=True)
    assert out["enemy_damage"] == 0
    assert out["enemy_kills"] == 0
    assert out.get("combat_reward_denied") is True
    assert out["combat_events"] and out["combat_events"][0]["reward_denied"] is True


def test_adder_room_301_and_shark_room_40e_deny() -> None:
    for room in ("301", "40E"):
        prev = {"room_id": room, "enemies": [{"slot": 0, "hp": 30}]}
        cur = {"room_id": room, "enemies": [{"slot": 0, "hp": 10}]}
        out = apply_combat_step_fields(prev, cur, attack=True)
        assert out["enemy_damage"] == 0, room
        assert out.get("combat_reward_denied") is True, room


def test_shark_type_name_denies_damage() -> None:
    prev = {
        "room_id": "40E",
        "enemies": [{"slot": 0, "hp": 200, "enemy_type": "shark"}],
    }
    cur = {
        "room_id": "40E",
        "enemies": [{"slot": 0, "hp": 150, "enemy_type": "shark"}],
    }
    out = apply_combat_step_fields(prev, cur, attack=True)
    assert out["enemy_damage"] == 0
    assert out["enemy_kills"] == 0
    assert out["combat_events"][0]["reward_denied"] is True


def test_type_id_wasp_adder_shark_deny() -> None:
    for tid in (0x0A, 0x0B, 0x0D):
        prev = {
            "room_id": "105",
            "enemies": [{"slot": 0, "hp": 40, "type_id": tid}],
        }
        cur = {
            "room_id": "105",
            "enemies": [{"slot": 0, "hp": 10, "type_id": tid}],
        }
        out = apply_combat_step_fields(prev, cur, attack=True)
        assert out["enemy_damage"] == 0, hex(tid)
        assert out["combat_events"][0]["reward_denied"] is True


def test_zombie_type_still_pays() -> None:
    prev = {
        "room_id": "104",
        "enemies": [{"slot": 0, "hp": 40, "type_id": 1}],
    }
    cur = {
        "room_id": "104",
        "enemies": [{"slot": 0, "hp": 28, "type_id": 1}],
    }
    out = apply_combat_step_fields(prev, cur, attack=True)
    assert out["enemy_damage"] == 12
    assert out["combat_events"][0].get("reward_denied") is False


def test_adder_type_name_denies_kill() -> None:
    prev = {
        "room_id": "405",
        "enemies": [{"slot": 1, "hp": 5, "type_name": "adder"}],
    }
    cur = {"room_id": "405", "enemies": []}
    out = apply_combat_step_fields(prev, cur, knife=True)
    assert out["enemy_damage"] == 0
    assert out["enemy_kills"] == 0
    assert out["combat_events"][0]["reward_denied"] is True
    assert out["combat_events"][0]["killed"] is True
