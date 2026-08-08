"""Live 48-slot box pollution: keys / deep scroll must not look 'empty'."""

from __future__ import annotations

from re1_rl.go_explore_capture import integrity_gate_ok
from re1_rl.item_box import BOX_SLOTS, box_pollution_reason
from re1_rl.progress import ProgressTracker


def test_box_pollution_key_in_deep_slot() -> None:
    box = [(0, 0)] * 48
    box[46] = (0x35, 1)  # shield_key
    assert box_pollution_reason(box) == "key_item_in_box:shield_key@46"


def test_box_pollution_deep_ammo() -> None:
    box = [(0, 0)] * 48
    box[20] = (0x0B, 15)
    assert box_pollution_reason(box) == "deep_box_item:handgun_bullets@20"


def test_box_pollution_clean_modeled_slots() -> None:
    box = [(0x0B, 15), (0x0B, 15)] + [(0, 0)] * (BOX_SLOTS - 2)
    assert box_pollution_reason(box) is None


def test_integrity_gate_rejects_key_in_box_cache() -> None:
    box = [[0, 0] for _ in range(48)]
    box[46] = [0x35, 1]
    state = {
        "in_control": True,
        "dead": False,
        "hp": 80,
        "room_id": "207",
        "box_cache": box,
    }
    ok, reason = integrity_gate_ok(state, ProgressTracker())
    assert not ok
    assert reason == "key_item_in_box:shield_key@46"
