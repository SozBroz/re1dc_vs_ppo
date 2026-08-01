"""Combat / world aux target packing and leakage rules."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, ATTACK_UP_ACTION
from re1_rl.combat_targets import (
    combat_target_to_outcome_vector,
    empty_combat_target,
    is_attack_action,
    pack_combat_target,
    pack_combat_target_from_info,
    pack_world_event_target_from_info,
)


def test_non_attack_has_false_mask() -> None:
    t = pack_combat_target(action_id=0, hit=True, damage=99, ammo_spent=1)
    assert t[8] == 0.0
    assert t[0] == -1.0
    y, m = combat_target_to_outcome_vector(t)
    assert m.sum() == 0
    assert y.sum() == 0


def test_unexecuted_heights_unlabeled() -> None:
    t = pack_combat_target(
        action_id=ATTACK_ACTION, hit=False, damage=0, ammo_spent=1, knife=False
    )
    y, m = combat_target_to_outcome_vector(t)
    assert m[0:6].sum() == 6
    assert m[6:].sum() == 0
    assert y[1] == 1.0  # wasted on neutral


def test_pack_from_info_attack() -> None:
    info = {
        "attack_report": {"outcome": "ok", "ammo_spent": 1, "weapon": "beretta"},
        "state": {"enemy_damage": 12, "enemy_kills": 0, "ammo_spent": 1},
        "hp": 100,
    }
    t = pack_combat_target_from_info(ATTACK_UP_ACTION, info, prev_hp=110)
    assert t[8] == 1.0
    assert t[0] == 1.0  # up
    assert t[1] == 1.0  # hit
    assert t[7] > 0  # player damage


def test_world_events_room_and_pickup() -> None:
    y, m = pack_world_event_target_from_info(
        7,
        {"room_id": "106", "new_items": ["helmet_key"], "state": {}},
        prev_room="100",
    )
    assert y[0] == 1.0
    assert y[1] == 1.0
    assert m[:10].sum() == 10
    assert m[10:].sum() == 0


def test_is_attack_action() -> None:
    assert is_attack_action(ATTACK_ACTION)
    assert is_attack_action(ATTACK_UP_ACTION)
    assert is_attack_action(ATTACK_DOWN_ACTION)
    assert not is_attack_action(1)
    assert empty_combat_target()[8] == 0.0
