"""Enemy HP delta helpers (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.enemy_combat import (
    HITSCAN_PENDING_FRAMES,
    PROJECTILE_PENDING_FRAMES,
    alive_enemy_count,
    apply_combat_step_fields,
    combat_enemy_count,
    enemy_combat_delta,
    enemy_combat_events,
    enemy_hp_by_slot,
    is_crow_combat_entity,
    pending_combat_window_frames,
    tick_pending_combat_credit,
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


def test_post_attack_credit_pays_delayed_hp_drop() -> None:
    """Dog HP posting one step after the attack macro still pays."""
    prev = {
        "room_id": "108",
        "pending_combat_frames": HITSCAN_PENDING_FRAMES,
        "enemies": [
            {"slot": 0, "hp": 32, "type_id": 15},
            {"slot": 1, "hp": 119, "type_id": 15},
        ],
    }
    cur = {
        "room_id": "108",
        "enemies": [
            {"slot": 0, "hp": 32, "type_id": 15},
            {"slot": 1, "hp": 92, "type_id": 15},
        ],
    }
    out = apply_combat_step_fields(prev, cur, credit_damage=True)
    assert out["enemy_damage"] == 27
    assert out["enemy_kills"] == 0
    assert "attack_missed" not in out
    assert "knife_swing_missed" not in out
    ticked = tick_pending_combat_credit(
        prev, out, step_emulated_frames=8, attack_outcome="ok"
    )
    assert ticked.get("credited_from_pending") is True
    assert int(ticked.get("pending_combat_frames") or 0) == 0
    # Without the pending window, same HP drop must still be denied (interact farm).
    denied = apply_combat_step_fields(prev, cur)
    assert denied["enemy_damage"] == 0
    assert denied["combat_events"] == []


def test_pending_fire_defers_miss_then_expires() -> None:
    prev = {"room_id": "108", "enemies": [{"slot": 0, "hp": 100, "type_id": 15}]}
    cur = {"room_id": "108", "enemies": [{"slot": 0, "hp": 100, "type_id": 15}]}
    fired = apply_combat_step_fields(prev, cur, attack=True)
    assert fired.get("attack_missed") is True
    armed = tick_pending_combat_credit(
        prev,
        fired,
        attack=True,
        ammo_spent=1,
        weapon_id=2,
        attack_outcome="no_damage",
        step_emulated_frames=8,
    )
    assert "attack_missed" not in armed
    assert int(armed["pending_combat_frames"]) == HITSCAN_PENDING_FRAMES

    # Tick almost to expiry with no HP drop.
    mid_prev = dict(armed)
    mid = {"room_id": "108", "enemies": [{"slot": 0, "hp": 100, "type_id": 15}]}
    mid = apply_combat_step_fields(mid_prev, mid, credit_damage=True)
    mid = tick_pending_combat_credit(
        mid_prev, mid, step_emulated_frames=HITSCAN_PENDING_FRAMES - 1
    )
    assert int(mid["pending_combat_frames"]) == 1

    end_prev = dict(mid)
    end = {"room_id": "108", "enemies": [{"slot": 0, "hp": 100, "type_id": 15}]}
    end = apply_combat_step_fields(end_prev, end, credit_damage=True)
    end = tick_pending_combat_credit(end_prev, end, step_emulated_frames=8)
    assert end.get("attack_missed") is True
    assert int(end.get("ammo_spent") or 0) == 1
    assert int(end.get("pending_miss_weapon_id") or 0) == 2
    assert int(end.get("pending_combat_frames") or 0) == 0


def test_bazooka_pending_window_is_two_seconds() -> None:
    assert pending_combat_window_frames(0x07) == PROJECTILE_PENDING_FRAMES
    assert pending_combat_window_frames(2) == HITSCAN_PENDING_FRAMES


def test_room_change_clears_pending_without_miss() -> None:
    prev = {
        "room_id": "108",
        "pending_combat_frames": 80,
        "pending_combat_ammo": 1,
        "pending_combat_weapon_id": 7,
        "enemies": [{"slot": 0, "hp": 100}],
    }
    cur = {"room_id": "107", "enemies": []}
    out = apply_combat_step_fields(prev, cur, credit_damage=True)
    out = tick_pending_combat_credit(prev, out, step_emulated_frames=8)
    assert int(out.get("pending_combat_frames") or 0) == 0
    assert not out.get("attack_missed")
    assert int(out.get("enemy_damage") or 0) == 0


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


def test_crow_active_byte_marks_combat_event() -> None:
    assert is_crow_combat_entity({"active_byte": 0x04})
    assert is_crow_combat_entity({"type_name": "crow"})
    assert not is_crow_combat_entity({"active_byte": 0x0F, "type_name": "zombie"})
    events = enemy_combat_events(
        [{"slot": 0, "hp": 20, "active_byte": 0x1C}],
        [{"slot": 0, "hp": 10, "active_byte": 0x1C}],
        room_id="117",
    )
    assert len(events) == 1
    assert events[0]["is_crow"] is True
    assert events[0]["damage"] == 10
