"""Planner use_box target loadout: surplus/needed slots and the box mask."""
from __future__ import annotations

from re1_rl.action_mask import (
    BOX_CLOSE_ACTION,
    BOX_DEPOSIT_ACTION,
    BOX_PHASE_CHOOSE,
    BOX_PHASE_DEPOSIT_SLOT,
    BOX_PHASE_WITHDRAW_SLOT,
    BOX_WITHDRAW_ACTION,
    SELECT_SLOT_BASE,
    WITHDRAW_ACTION_BASE,
    action_mask,
)
from re1_rl.box_target import (
    inventory_matches_target,
    needed_box_slots,
    surplus_inventory_slots,
)
from re1_rl.env import ACTION_NAMES
from re1_rl.planner_loyal import (
    PLANNER_OP_TYPES,
    PLANNER_QUEUE_DIM,
    PlannerLoyalQueue,
    encode_planner_queue,
)

N = len(ACTION_NAMES)

TARGET = [
    {"item": "beretta", "qty": 0, "slot": 1},
    {"item": "handgun_bullets", "qty": 30, "slot": 2},
    {"item": "chemical", "qty": 1, "slot": 3},
    {"item": None, "qty": 0, "slot": 4},
    {"item": None, "qty": 0, "slot": 5},
    {"item": None, "qty": 0, "slot": 6},
    {"item": None, "qty": 0, "slot": 7},
    {"item": None, "qty": 0, "slot": 8},
]


def _full_start() -> list[tuple[int, int]]:
    # knife, beretta, 30, shield, herb, herb, chemical, empty
    return [
        (0x01, 0),
        (0x02, 0),
        (0x0B, 30),
        (0x35, 1),
        (0x44, 1),
        (0x44, 1),
        (0x26, 1),
        (0, 0),
    ]


def test_surplus_is_knife_shield_and_herbs() -> None:
    surplus = surplus_inventory_slots(_full_start(), TARGET)
    assert surplus == [0, 3, 4, 5]


def test_no_withdraw_when_target_already_on_person() -> None:
    box = [(0x0B, 15)] + [(0, 0)] * 15
    assert needed_box_slots(_full_start(), box, TARGET) == []


def test_withdraw_needed_clip_when_ammo_short() -> None:
    inv = [(0x02, 0), (0x26, 1)] + [(0, 0)] * 6
    box = [(0x0B, 30)] + [(0, 0)] * 15
    assert needed_box_slots(inv, box, TARGET) == [0]


def test_extra_clip_is_not_surplus() -> None:
    inv = [(0x02, 0), (0x0B, 45), (0x26, 1)] + [(0, 0)] * 5
    assert surplus_inventory_slots(inv, TARGET) == []
    assert inventory_matches_target(inv, TARGET)


def test_loaded_gun_plus_clip_meets_minimum() -> None:
    # 15 in the beretta + 15 spare = 30. Reload must not force a deposit.
    inv = [(0x02, 15), (0x0B, 15), (0x26, 1)] + [(0, 0)] * 5
    assert inventory_matches_target(inv, TARGET)
    assert surplus_inventory_slots(inv, TARGET) == []
    assert needed_box_slots(inv, [(0x0B, 15)] + [(0, 0)] * 15, TARGET) == []


def test_loaded_gun_plus_extra_clip_still_matches() -> None:
    inv = [(0x02, 15), (0x0B, 30), (0x26, 1)] + [(0, 0)] * 5
    assert inventory_matches_target(inv, TARGET)
    assert surplus_inventory_slots(inv, TARGET) == []


def test_loaded_only_below_minimum_still_needs_clip() -> None:
    inv = [(0x02, 15), (0x26, 1)] + [(0, 0)] * 6
    box = [(0x0B, 30)] + [(0, 0)] * 15
    assert inventory_matches_target(inv, TARGET) is False
    assert needed_box_slots(inv, box, TARGET) == [0]


def test_match_ignores_slot_order() -> None:
    inv = [
        (0x26, 1),
        (0x0B, 30),
        (0x02, 0),
        (0, 0),
        (0, 0),
        (0, 0),
        (0, 0),
        (0, 0),
    ]
    assert inventory_matches_target(inv, TARGET)


def test_close_only_without_target() -> None:
    m = action_mask(
        N,
        None,
        inventory=_full_start(),
        box=[(0x0B, 15)] + [(0, 0)] * 15,
        box_ui_open=True,
        box_phase=BOX_PHASE_CHOOSE,
        in_control=False,
        room_id="118",
        box_close_only=True,
    )
    assert m[BOX_CLOSE_ACTION]
    assert not m[BOX_DEPOSIT_ACTION]
    assert not m[BOX_WITHDRAW_ACTION]


def test_target_mask_only_deposits_surplus() -> None:
    m = action_mask(
        N,
        None,
        inventory=_full_start(),
        box=[(0, 0)] * 16,
        box_ui_open=True,
        box_phase=BOX_PHASE_CHOOSE,
        in_control=False,
        room_id="118",
        box_target_held=TARGET,
    )
    assert m[BOX_DEPOSIT_ACTION]
    assert not m[BOX_WITHDRAW_ACTION]
    assert not m[BOX_CLOSE_ACTION]
    pick = action_mask(
        N,
        None,
        inventory=_full_start(),
        box=[(0, 0)] * 16,
        box_ui_open=True,
        box_phase=BOX_PHASE_DEPOSIT_SLOT,
        in_control=False,
        room_id="118",
        box_target_held=TARGET,
    )
    assert pick[SELECT_SLOT_BASE + 0]
    assert pick[SELECT_SLOT_BASE + 3]
    assert pick[SELECT_SLOT_BASE + 4]
    assert not pick[SELECT_SLOT_BASE + 1]
    assert not pick[SELECT_SLOT_BASE + 2]
    assert not pick[SELECT_SLOT_BASE + 6]


def test_target_mask_only_withdraws_needed_ammo() -> None:
    inv = [(0x02, 0), (0x26, 1)] + [(0, 0)] * 6
    box = [(0x01, 0), (0x0B, 30)] + [(0, 0)] * 14
    m = action_mask(
        N,
        None,
        inventory=inv,
        box=box,
        box_ui_open=True,
        box_phase=BOX_PHASE_WITHDRAW_SLOT,
        in_control=False,
        room_id="118",
        box_target_held=TARGET,
    )
    assert not m[WITHDRAW_ACTION_BASE + 0]
    assert m[WITHDRAW_ACTION_BASE + 1]


def test_matched_target_only_closes() -> None:
    inv = [(0x02, 0), (0x0B, 30), (0x26, 1)] + [(0, 0)] * 5
    m = action_mask(
        N,
        None,
        inventory=inv,
        box=[(0x35, 1)] + [(0, 0)] * 15,
        box_ui_open=True,
        box_phase=BOX_PHASE_CHOOSE,
        in_control=False,
        room_id="118",
        box_target_held=TARGET,
    )
    assert m[BOX_CLOSE_ACTION]
    assert not m[BOX_DEPOSIT_ACTION]
    assert not m[BOX_WITHDRAW_ACTION]


def test_use_box_with_target_completes_on_close_not_open() -> None:
    q = PlannerLoyalQueue(
        {
            "chunk_id": "test_box",
            "leave_118": {"held_on_exit": TARGET},
            "steps": [
                {"n": 1, "op": "use_box", "room_id": "118"},
                {"n": 2, "op": "traverse", "edge_id": "118->10B"},
            ],
        }
    )
    prev = {"room_id": "118", "inventory_slots": [("knife", 0)]}
    matched = {
        "room_id": "118",
        "inventory_slots": [
            ("beretta", 0),
            ("handgun_bullets", 30),
            ("chemical", 1),
        ],
    }
    opened = q.evaluate_transition(
        prev_state=prev, state=matched, box_opened=True
    )
    assert opened["step_success"] is False
    closed = q.evaluate_transition(
        prev_state=matched, state=matched, box_closed=True
    )
    assert closed["step_success"] is True
    assert q.current["op"] == "traverse"


def _box_chunk() -> dict:
    return {
        "chunk_id": "test_go_box",
        "leave_118": {"held_on_exit": TARGET},
        "steps": [
            {"n": 1, "op": "go_to_box", "room_id": "118"},
            {"n": 2, "op": "use_box", "room_id": "118"},
            {"n": 3, "op": "traverse", "edge_id": "118->10B"},
        ],
    }


def test_go_to_box_allows_hops_and_completes_on_arrival() -> None:
    q = PlannerLoyalQueue(_box_chunk())
    q.note_start_inventory({"room_id": "10B", "inventory_slots": [("knife", 0)]})
    mid = q.evaluate_transition(
        prev_state={"room_id": "10B"},
        state={"room_id": "10A"},
    )
    assert mid["divert"] is False
    assert mid["step_success"] is False
    assert q.current["op"] == "go_to_box"
    arrived = q.evaluate_transition(
        prev_state={"room_id": "10B"},
        state={"room_id": "118"},
    )
    assert arrived["step_success"] is True
    assert q.current["op"] == "use_box"


def test_go_to_box_skipped_when_episode_starts_in_box() -> None:
    q = PlannerLoyalQueue(_box_chunk())
    q.note_start_inventory({"room_id": "118", "inventory_slots": [("knife", 0)]})
    result = q.evaluate_transition(
        prev_state={"room_id": "118"},
        state={"room_id": "118"},
    )
    assert q.current["op"] == "use_box"
    assert result["step_success"] is False


def test_live_chunk_use_box_unlocks_shield_key_bank() -> None:
    from re1_rl.item_box import box_pollution_reason, can_deposit

    q = PlannerLoyalQueue()
    q.seek(24)
    assert q.current is not None
    assert q.current["op"] == "use_box"
    assert q.allowed_banked_key_names() == frozenset({"shield_key"})
    inv = [
        (0x01, 0),
        (0x02, 0),
        (0x0B, 30),
        (0x35, 1),
        (0x44, 1),
        (0x44, 1),
        (0x26, 1),
        (0, 0),
    ]
    box = [(0, 0)] * 16
    ok, reason = can_deposit(
        inv, box, 3, room_id="118", allowed_key_ids=q.allowed_banked_key_ids()
    )
    assert ok, reason
    dirty = [(0x35, 1)] + [(0, 0)] * 15
    assert (
        box_pollution_reason(
            dirty,
            room_id="118",
            allowed_key_names=q.allowed_banked_key_names(),
        )
        is None
    )
    assert box_pollution_reason(dirty, room_id="118") == "key_item_in_box:shield_key@0"


def test_go_to_box_encodes_as_use_box_one_hot() -> None:
    q = PlannerLoyalQueue(_box_chunk())
    vec = encode_planner_queue(q)
    assert len(vec) == PLANNER_QUEUE_DIM
    use_box_idx = PLANNER_OP_TYPES.index("use_box")
    assert vec[1 + use_box_idx] == 1.0
    assert sum(vec[1 : 1 + len(PLANNER_OP_TYPES)]) == 1.0
