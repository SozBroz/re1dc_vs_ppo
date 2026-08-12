"""Yawn storeroom (118) box prep: wind crest deposit + clean weapon/ammo bank."""

from __future__ import annotations

from re1_rl.item_box import (
    BOX_SLOTS,
    BOX_SLOTS_LIVE,
    box_pollution_reason,
    can_deposit,
    can_withdraw,
    is_deposit_allowed_item,
    plan_deposit,
)
from re1_rl.yawn_box_prep_checkpoint import (
    WIND_CREST_ITEM_ID,
    yawn_box_prep_box_pollution_reason,
    yawn_box_prep_capture_ready,
    yawn_box_prep_capture_room_ok,
    yawn_box_prep_ready,
    yawn_box_weapon_ammo_clear,
    should_suppress_wrong_room,
)


def _box() -> list[tuple[int, int]]:
    return [(0, 0)] * BOX_SLOTS_LIVE


def test_wind_crest_deposit_allowed_only_in_room_118() -> None:
    assert is_deposit_allowed_item(WIND_CREST_ITEM_ID, "118")
    assert not is_deposit_allowed_item(WIND_CREST_ITEM_ID, "100")
    assert not is_deposit_allowed_item(0x35, "118")  # shield_key


def test_can_deposit_wind_crest_at_118() -> None:
    inv = [(WIND_CREST_ITEM_ID, 1)] + [(0, 0)] * 7
    box = [(0, 0)] * BOX_SLOTS
    ok, reason = can_deposit(inv, box, 0, room_id="118", enforce_allowlist=True)
    assert ok and reason == ""


def test_full_pack_select_slot_7_deposit_masked() -> None:
    from re1_rl.action_mask import (
        BOX_DEPOSIT_ACTION,
        BOX_PHASE_DEPOSIT_SLOT,
        SELECT_SLOT_BASE,
        action_mask,
    )
    from re1_rl.env import ACTION_NAMES
    from re1_rl.item_box_ui_macro import box_deposit_slot_reachable

    inv = [
        (0x02, 15),
        (0x0B, 24),
        (0x35, 1),
        (0x03, 7),
        (0x11, 6),
        (0x34, 4),
        (0x0C, 2),
        (WIND_CREST_ITEM_ID, 1),
    ]
    box = [(0, 0)] * BOX_SLOTS
    box[1] = (0x01, 0)
    box[2] = (0x07, 4)
    assert not is_deposit_allowed_item(0x0C, "118")  # shotgun_shells
    assert not is_deposit_allowed_item(0x0B, "118")  # handgun_bullets
    assert not is_deposit_allowed_item(0x11, "118")  # acid_rounds
    assert is_deposit_allowed_item(WIND_CREST_ITEM_ID, "118")
    assert box_deposit_slot_reachable(inv, 6, from_slot=0)
    assert box_deposit_slot_reachable(inv, 7, from_slot=0)
    ok6, why6 = can_deposit(inv, box, 6, room_id="118", enforce_allowlist=True)
    ok7, _ = can_deposit(inv, box, 7, room_id="118", enforce_allowlist=True)
    assert not ok6 and why6 == "not_allowlisted"
    assert ok7

    n = len(ACTION_NAMES)
    mask = action_mask(
        n,
        None,
        inventory=inv,
        box=box,
        box_ui_open=True,
        box_phase=BOX_PHASE_DEPOSIT_SLOT,
        room_id="118",
    )
    assert not mask[SELECT_SLOT_BASE + 6]
    assert mask[SELECT_SLOT_BASE + 7]
    choose = action_mask(
        n,
        None,
        inventory=inv,
        box=box,
        box_ui_open=True,
        box_phase=0,
        room_id="118",
    )
    assert choose[BOX_DEPOSIT_ACTION]


def test_deposit_crest_then_withdraw_bazooka() -> None:
    inv = [
        (0x02, 15),
        (0x0B, 24),
        (0x35, 1),
        (0x03, 7),
        (0x11, 6),
        (0x34, 4),
        (0x0C, 2),
        (WIND_CREST_ITEM_ID, 1),
    ]
    box = [(0, 0)] * BOX_SLOTS
    box[1] = (0x01, 0)
    box[2] = (0x07, 4)
    assert box_pollution_reason(box, room_id="118") is None
    ok_dep, why_dep = can_deposit(inv, box, 7, room_id="118", enforce_allowlist=True)
    assert ok_dep and why_dep == ""
    ok_wd0, why_wd0 = can_withdraw(inv, box, 2)
    assert not ok_wd0 and why_wd0 == "inventory_full"

    new_inv, new_box, moved = plan_deposit(inv, box, 7)
    assert moved == 1
    assert new_inv[7] == (0, 0)
    assert any(iid == WIND_CREST_ITEM_ID for iid, _ in new_box)
    ok_wd, why_wd = can_withdraw(new_inv, new_box, 2)
    assert ok_wd and why_wd == ""
    assert box_pollution_reason(new_box, room_id="118") is None


def test_yawn_box_prep_allows_wind_crest_and_knife_rejects_ammo() -> None:
    box = _box()
    box[0] = (WIND_CREST_ITEM_ID, 1)
    box[1] = (0x01, 0)
    assert yawn_box_prep_box_pollution_reason(box) is None

    dirty = list(box)
    dirty[2] = (0x0B, 15)
    assert yawn_box_prep_box_pollution_reason(dirty) == "yawn_box_weapon_ammo:handgun_bullets@2"

    gun = list(box)
    gun[2] = (0x02, 1)
    assert yawn_box_prep_box_pollution_reason(gun) == "yawn_box_weapon_ammo:beretta@2"


def test_generic_pollution_allows_banked_wind_crest_and_bazooka() -> None:
    box = _box()
    box[0] = (WIND_CREST_ITEM_ID, 1)
    box[1] = (0x01, 0)
    box[2] = (0x07, 4)
    assert box_pollution_reason(box) is None
    assert box_pollution_reason(box, room_id="10B") is None


def test_yawn_box_prep_capture_requires_wind_in_box_not_on_person() -> None:
    box = _box()
    box[0] = (WIND_CREST_ITEM_ID, 1)
    assert yawn_box_prep_capture_ready(box, []) is None
    assert yawn_box_prep_capture_ready(box, ["wind_crest"]) == "wind_crest_still_held"
    assert yawn_box_prep_capture_ready(_box(), []) == "wind_crest_not_in_box"


def test_yawn_box_weapon_ammo_clear() -> None:
    box = _box()
    box[0] = (0x01, 0)
    assert yawn_box_weapon_ammo_clear(box)
    box[1] = (0x10, 6)
    assert not yawn_box_weapon_ammo_clear(box)


def test_planner_yawn_box_prep_succeeds_on_leave_to_10b() -> None:
    from re1_rl.progress import ProgressTracker
    from tests.test_yawn_rails import _idx, _planner, _state

    planner = _planner(start_index=_idx("yawn_box_prep_118"))
    progress = ProgressTracker()
    box = [(0, 0)] * BOX_SLOTS_LIVE
    box[0] = (WIND_CREST_ITEM_ID, 1)

    still_in = _state("118")
    still_in["lab_timer"] = 0
    still_in["inventory"] = ["shield_key", "shotgun"]
    still_in["box_cache"] = box
    assert not planner.advance_if_success(still_in, progress=progress)

    dirty_leave = _state("10B")
    dirty_leave["lab_timer"] = 0
    dirty_leave["inventory"] = ["shield_key", "shotgun", "wind_crest"]
    dirty_leave["box_cache"] = [(0, 0)] * BOX_SLOTS_LIVE
    dirty_leave["box_cache"][2] = (0x07, 4)
    assert not planner.advance_if_success(
        dirty_leave, progress=progress, prev_state=_state("118")
    )

    ready = _state("10B")
    ready["lab_timer"] = 0
    ready["inventory"] = ["shield_key", "shotgun", "bazooka_acid"]
    ready["box_cache"] = box
    assert planner.advance_if_success(
        ready, progress=progress, prev_state=_state("118")
    )
    assert planner.current_objective()["checkpoint_id"] == "east_stairs_101_to_yawn"


def test_suppress_wrong_room_only_when_prep_ready() -> None:
    from tests.test_yawn_rails import _idx, _planner, _state

    planner = _planner(start_index=_idx("yawn_box_prep_118"))
    box = [(0, 0)] * BOX_SLOTS_LIVE
    box[0] = (WIND_CREST_ITEM_ID, 1)
    ready = _state("10B")
    ready["lab_timer"] = 0
    ready["inventory"] = ["shield_key", "shotgun"]
    ready["box_cache"] = box
    assert should_suppress_wrong_room(planner, "118", "10B", ready)
    assert yawn_box_prep_ready(ready)

    dirty = dict(ready)
    dirty["inventory"] = ["shield_key", "shotgun", "wind_crest"]
    dirty["box_cache"] = [(0, 0)] * BOX_SLOTS_LIVE
    dirty["box_cache"][2] = (0x07, 4)
    assert not should_suppress_wrong_room(planner, "118", "10B", dirty)
    assert not should_suppress_wrong_room(planner, "118", "10A", ready)
    assert yawn_box_prep_capture_room_ok("yawn_box_prep_118", "10B", "118")
    assert not yawn_box_prep_capture_room_ok("yawn_box_prep_118", "10A", "118")
