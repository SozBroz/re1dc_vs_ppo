"""Uniform ammo norm, weapon_card room bonuses, last_attack one-step TTL."""

from __future__ import annotations

import numpy as np
import pytest

from re1_rl.action_mask import (
    ATTACK_ACTION,
    ATTACK_DOWN_ACTION,
    ATTACK_UP_ACTION,
    KNIFE_SWING_ACTION,
)
from re1_rl.weapon_damage import (
    AMMO_QTY_NORM,
    LAST_ATTACK_DIM,
    LAST_ATTACK_MACRO_DOWN,
    LAST_ATTACK_MACRO_NEUTRAL,
    LAST_ATTACK_MACRO_OFFSET,
    LAST_ATTACK_MACRO_UP,
    N_LAST_ATTACK_MACROS,
    WEAPON_CARD_DIM,
    ammo_qty_norm,
    empty_last_attack,
    encode_weapon_card,
    equipped_clip_from_inventory_slots,
    last_attack_macro_from_action,
    pack_last_attack,
    room_bonus_flags,
)
from re1_rl.obs_encoder import encode_box, encode_inventory_slots


def _macro_slice(v: np.ndarray) -> np.ndarray:
    return v[LAST_ATTACK_MACRO_OFFSET : LAST_ATTACK_MACRO_OFFSET + N_LAST_ATTACK_MACROS]


def test_ammo_qty_norm_shared_scale() -> None:
    assert AMMO_QTY_NORM == 255.0
    assert ammo_qty_norm(0) == 0.0
    assert ammo_qty_norm(15) == pytest.approx(15 / 255.0)
    assert ammo_qty_norm(255) == 1.0
    assert ammo_qty_norm(999) == 1.0


def test_inventory_and_box_use_ammo_norm() -> None:
    inv = encode_inventory_slots([("beretta", 15)])
    assert inv[1] == pytest.approx(15 / AMMO_QTY_NORM)
    box = encode_box([(0x02, 15)], in_box_room=False)
    assert box[1] == pytest.approx(15 / AMMO_QTY_NORM)


def test_weapon_card_clip_matches_inventory_scale() -> None:
    clip = 14
    card = encode_weapon_card(weapon_id=0x02, equipped_clip=clip, room_id="105")
    inv = encode_inventory_slots([("beretta", clip)])
    assert card.shape == (WEAPON_CARD_DIM,)
    assert card[0] == pytest.approx(inv[1])
    assert card[0] == pytest.approx(ammo_qty_norm(clip))


def test_weapon_card_shotgun_range_and_nominal() -> None:
    card = encode_weapon_card(weapon_id=0x03, equipped_clip=7, room_id="105")
    assert card[1] == pytest.approx(15 / 255.0)
    assert card[2] == pytest.approx(25 / 255.0)
    assert card[3] == 1.0  # range_scaled
    assert card[4] == 1.0  # round_none


def test_weapon_card_bazooka_round_types() -> None:
    acid = encode_weapon_card(weapon_id=0x07, equipped_clip=1, room_id="105")
    flame = encode_weapon_card(weapon_id=0x09, equipped_clip=1, room_id="105")
    expl = encode_weapon_card(weapon_id=0x08, equipped_clip=1, room_id="105")
    rocket = encode_weapon_card(weapon_id=0x0A, equipped_clip=1, room_id="105")
    assert acid[5] == 1.0  # round_acid
    assert flame[6] == 1.0  # round_flame
    assert expl[7] == 1.0  # round_explosive
    assert rocket[7] == 1.0


@pytest.mark.parametrize(
    "room,weapon,acid_active,flame_active",
    [
        ("210", 0x07, 1.0, 0.0),
        ("20C", 0x07, 1.0, 0.0),
        ("514", 0x07, 1.0, 0.0),
        ("513", 0x07, 1.0, 0.0),
        ("40C", 0x09, 0.0, 1.0),
        ("30C", 0x09, 0.0, 1.0),
        ("210", 0x09, 0.0, 0.0),  # acid room, wrong round
        ("40C", 0x07, 0.0, 0.0),  # flame room, wrong round
        ("105", 0x07, 0.0, 0.0),  # neither
        ("20B", 0x07, 0.0, 0.0),  # front lesson room excluded
    ],
)
def test_room_bonus_flags(
    room: str, weapon: int, acid_active: float, flame_active: float
) -> None:
    flags = room_bonus_flags(room, weapon)
    card = encode_weapon_card(weapon_id=weapon, equipped_clip=1, room_id=room)
    assert flags["acid_bonus_active"] == acid_active
    assert flags["flame_bonus_active"] == flame_active
    assert card[10] == acid_active
    assert card[11] == flame_active


def test_pack_last_attack_hit_and_ammo_norm() -> None:
    events = [
        {"slot": 0, "hp_before": 60, "hp_after": 40, "damage": 20, "killed": False},
    ]
    v = pack_last_attack(
        knife=False,
        attack=True,
        combat_events=events,
        enemy_damage=20,
        enemy_kills=0,
        clip_before=15,
        clip_after=14,
        ammo_spent=1,
        enemies_before=[{"slot": 0, "type_id": 1, "hp": 60}],
        action_id=ATTACK_ACTION,
    )
    assert v.shape == (LAST_ATTACK_DIM,)
    assert LAST_ATTACK_DIM == 16
    assert v[0] == 1.0  # valid
    assert v[1] == 1.0  # hit
    assert v[2] == pytest.approx(ammo_qty_norm(15))
    assert v[3] == pytest.approx(ammo_qty_norm(14))
    assert v[4] == pytest.approx(ammo_qty_norm(1))
    assert v[5] == pytest.approx(20 / 255.0)
    assert v[7] == pytest.approx(60 / 255.0)
    assert v[8] == pytest.approx(40 / 255.0)
    assert list(_macro_slice(v)) == [1.0, 0.0, 0.0]


def test_pack_last_attack_miss() -> None:
    v = pack_last_attack(
        knife=False,
        attack=True,
        combat_events=[],
        enemy_damage=0,
        enemy_kills=0,
        clip_before=10,
        clip_after=9,
        ammo_spent=1,
        action_id=ATTACK_ACTION,
    )
    assert v[0] == 1.0
    assert v[1] == 0.0  # miss
    assert v[4] == pytest.approx(ammo_qty_norm(1))
    assert list(_macro_slice(v)) == [1.0, 0.0, 0.0]


def test_pack_last_attack_knife_zeros_clip() -> None:
    v = pack_last_attack(
        knife=True,
        attack=False,
        combat_events=[
            {"slot": 1, "hp_before": 8, "hp_after": 6, "damage": 2, "killed": False}
        ],
        enemy_damage=2,
        enemy_kills=0,
        clip_before=99,
        clip_after=99,
        ammo_spent=0,
        action_id=KNIFE_SWING_ACTION,
    )
    assert v[0] == 1.0
    assert v[1] == 1.0
    assert v[2] == 0.0
    assert v[3] == 0.0
    assert v[4] == 0.0
    # knife_swing → down height; weapon from equip, not this one-hot
    assert list(_macro_slice(v)) == [0.0, 0.0, 1.0]


@pytest.mark.parametrize(
    "action_id,macro_idx",
    [
        (KNIFE_SWING_ACTION, LAST_ATTACK_MACRO_DOWN),
        (ATTACK_ACTION, LAST_ATTACK_MACRO_NEUTRAL),
        (ATTACK_UP_ACTION, LAST_ATTACK_MACRO_UP),
        (ATTACK_DOWN_ACTION, LAST_ATTACK_MACRO_DOWN),
    ],
)
def test_pack_last_attack_macro_one_hot(action_id: int, macro_idx: int) -> None:
    assert last_attack_macro_from_action(action_id) == macro_idx
    knife = action_id == KNIFE_SWING_ACTION
    v = pack_last_attack(
        knife=knife,
        attack=not knife,
        combat_events=[],
        enemy_damage=0,
        enemy_kills=0,
        clip_before=5,
        clip_after=4 if not knife else 5,
        ammo_spent=0 if knife else 1,
        action_id=action_id,
    )
    expected = [0.0] * N_LAST_ATTACK_MACROS
    expected[macro_idx] = 1.0
    assert list(_macro_slice(v)) == expected


def test_empty_last_attack_zeros_macro() -> None:
    cleared = empty_last_attack()
    assert cleared.shape == (LAST_ATTACK_DIM,)
    assert np.all(_macro_slice(cleared) == 0.0)


def test_last_attack_one_step_ttl_pattern() -> None:
    """Env clears at step start; pack fills after attack — next clear empties."""
    filled = pack_last_attack(
        knife=False,
        attack=True,
        combat_events=[],
        enemy_damage=0,
        enemy_kills=0,
        clip_before=5,
        clip_after=4,
        ammo_spent=1,
        action_id=ATTACK_UP_ACTION,
    )
    assert filled[0] == 1.0
    assert list(_macro_slice(filled)) == [0.0, 1.0, 0.0]
    cleared = empty_last_attack()
    assert cleared.shape == (LAST_ATTACK_DIM,)
    assert np.all(cleared == 0.0)


def test_equipped_clip_from_named_slots() -> None:
    assert equipped_clip_from_inventory_slots([("beretta", 12)], 0x02) == 12
    assert equipped_clip_from_inventory_slots([("knife", 1)], 0x01) == 0
    assert equipped_clip_from_inventory_slots([(0x02, 8)], 0x02) == 8
