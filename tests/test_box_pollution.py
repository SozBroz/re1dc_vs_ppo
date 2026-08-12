"""Live 48-slot box pollution: keys / deep scroll must not look 'empty'."""

from __future__ import annotations

from re1_rl.go_explore_capture import integrity_gate_ok
from re1_rl.item_box import BOX_SLOTS, BOX_SLOTS_LIVE, box_pollution_reason, plan_deposit
from re1_rl.item_box_ui_macro import (
    BOX_LIST_HOME_UPS,
    _deep_box_changed,
    _first_empty_modeled_slot,
)
from re1_rl.obs_encoder import encode_box
from re1_rl.progress import ProgressTracker


def test_box_pollution_key_in_deep_slot() -> None:
    box = [(0, 0)] * 48
    box[46] = (0x35, 1)  # shield_key
    assert box_pollution_reason(box) == "key_item_in_box:shield_key@46"


def test_box_pollution_chemical_is_key_item() -> None:
    box = [(0, 0)] * 48
    box[0] = (0x26, 1)  # chemical
    assert box_pollution_reason(box) == "key_item_in_box:chemical@0"
    box2 = [(0, 0)] * 48
    box2[40] = (0x26, 1)
    assert box_pollution_reason(box2) == "key_item_in_box:chemical@40"


def test_box_pollution_deep_ammo() -> None:
    box = [(0, 0)] * 48
    box[20] = (0x0B, 15)
    assert box_pollution_reason(box) == "deep_box_item:handgun_bullets@20"


def test_box_pollution_clean_modeled_slots() -> None:
    box = [(0x0B, 15), (0x0B, 15)] + [(0, 0)] * (BOX_SLOTS - 2)
    assert box_pollution_reason(box) is None


def test_box_pollution_beretta_in_modeled_slot() -> None:
    box = [(0x0B, 15), (0x02, 14), (0x01, 0)] + [(0, 0)] * (BOX_SLOTS - 3)
    assert box_pollution_reason(box) == "disallowed_item_in_box:beretta@1"


def test_box_pollution_knife_bank_ok() -> None:
    box = [(0x01, 0), (0x44, 1)] + [(0, 0)] * (BOX_SLOTS - 2)
    assert box_pollution_reason(box) is None


def test_box_pollution_bazooka_bank_ok() -> None:
    box = [(0x07, 6), (0x11, 6), (0x0A, 1)] + [(0, 0)] * (BOX_SLOTS - 3)
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


def test_box_list_home_covers_modeled_window() -> None:
    assert BOX_LIST_HOME_UPS == BOX_SLOTS - 1
    assert BOX_LIST_HOME_UPS == 15
    # Live length is wider, but excess Ups break transfers (do not home to 47).
    assert BOX_SLOTS_LIVE > BOX_SLOTS


def test_deposit_dest_is_lowest_empty_modeled_slot() -> None:
    inv = [(0x01, 0)] + [(0, 0)] * 7
    box = [(0x0B, 15)] * 5 + [(0, 0)] * (BOX_SLOTS - 5)
    assert _first_empty_modeled_slot(box) == 5
    _new_inv, new_box, moved = plan_deposit(inv, box, 0)
    assert moved == 1
    assert new_box[5] == (0x01, 0)
    assert all(new_box[i] == box[i] for i in range(5))
    assert _first_empty_modeled_slot(new_box) == 6


def test_chemical_never_depositable() -> None:
    from re1_rl.item_box import can_deposit, is_deposit_allowed_item, is_key_item_id
    from re1_rl.yawn_box_prep_checkpoint import WIND_CREST_ITEM_ID

    assert is_key_item_id(0x26)
    assert not is_deposit_allowed_item(0x26, "118")
    assert is_deposit_allowed_item(WIND_CREST_ITEM_ID, "118")
    assert not is_deposit_allowed_item(WIND_CREST_ITEM_ID, "100")
    assert not is_deposit_allowed_item(0x26, "100")
    inv = [(0x26, 1)] + [(0, 0)] * 7
    box = [(0, 0)] * BOX_SLOTS
    for room in ("100", "118", None):
        ok, reason = can_deposit(inv, box, 0, room_id=room, enforce_allowlist=True)
        assert not ok and reason == "key_item", (room, reason)
        # Keys stay refused even when allowlist policy is off.
        ok2, reason2 = can_deposit(inv, box, 0, room_id=room, enforce_allowlist=False)
        assert not ok2 and reason2 == "key_item", (room, reason2)


def test_box_list_home_from_zero_is_noop() -> None:
    """Ups from slot 0 wrap to live slot 33 — home must not tap when from_slot=0."""
    assert BOX_LIST_HOME_UPS == 15
    # Document the wrap that produced memlog chemical@33.
    assert (0 - 15) % 48 == 33


def test_box_inventory_nav_vertical_any_occupancy() -> None:
    from re1_rl.item_box_ui_macro import box_inventory_nav_moves

    # Col-0 path ignores occupancy for Up/Down.
    inv = [(0x01, 0), (0x02, 1), (0x0B, 15), (0x35, 1), (0x44, 1), (0x03, 4), (0, 0), (0, 0)]
    assert box_inventory_nav_moves(0, 6, inv) == ["down", "down", "down"]
    assert box_inventory_nav_moves(6, 0, inv) == ["up", "up", "up"]


def test_box_inventory_nav_every_slot_from_full_pack() -> None:
    from re1_rl.item_box_ui_macro import (
        box_deposit_slot_reachable,
        box_inventory_nav_moves,
        first_reachable_empty_inventory_slot,
    )

    # Full pack — wind crest at slot 7 must still be a legal D-pad target.
    full = [
        (0x02, 15),
        (0x0B, 24),
        (0x35, 1),
        (0x03, 7),
        (0x11, 6),
        (0x34, 4),
        (0x0C, 2),
        (0x29, 1),
    ]
    assert box_inventory_nav_moves(0, 7, full) == ["down", "down", "down", "right"]
    assert box_inventory_nav_moves(6, 7, full) == ["right"]
    assert box_inventory_nav_moves(0, 1, full) == ["right"]
    for slot in range(8):
        assert box_deposit_slot_reachable(full, slot, from_slot=0)

    # Only empty is odd-col slot 7; withdraw dest must still be reachable.
    odd_empty = list(full)
    odd_empty[7] = (0, 0)
    assert box_inventory_nav_moves(0, 7, odd_empty) == [
        "down",
        "down",
        "down",
        "right",
    ]
    assert first_reachable_empty_inventory_slot(odd_empty, from_slot=0) == 7


def test_encode_box_blind_to_deep_slots() -> None:
    box = [(0, 0)] * 48
    box[30] = (0x26, 1)
    vec = encode_box(box, in_box_room=True)
    # Modeled slots stay empty in obs even though live RAM is polluted.
    assert float(vec[0]) == 0.0
    assert float(vec[32]) == 1.0  # free_slots / 16 == 1.0
    assert box_pollution_reason(box) == "key_item_in_box:chemical@30"


def test_deep_box_changed_detects_tail_writes() -> None:
    before = [(0, 0)] * 48
    after = list(before)
    after[40] = (0x01, 0)
    assert _deep_box_changed(before, after)
    after2 = list(before)
    after2[3] = (0x01, 0)
    assert not _deep_box_changed(before, after2)
