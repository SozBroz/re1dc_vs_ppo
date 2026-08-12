"""Yawn storeroom (118) box prep: wind crest deposit + clean weapon/ammo bank."""

from __future__ import annotations

from re1_rl.item_box import (
    BOX_SLOTS,
    BOX_SLOTS_LIVE,
    can_deposit,
    is_deposit_allowed_item,
)
from re1_rl.yawn_box_prep_checkpoint import (
    WIND_CREST_ITEM_ID,
    yawn_box_prep_box_pollution_reason,
    yawn_box_prep_capture_ready,
    yawn_box_weapon_ammo_clear,
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


def test_planner_yawn_box_prep_success_conditions() -> None:
    from re1_rl.progress import ProgressTracker
    from tests.test_yawn_rails import _idx, _planner, _state

    planner = _planner(start_index=_idx("yawn_box_prep_118"))
    progress = ProgressTracker()

    incomplete = _state("118")
    incomplete["lab_timer"] = 0
    incomplete["inventory"] = ["shield_key", "shotgun", "wind_crest"]
    incomplete["box_cache"] = [(0, 0)] * BOX_SLOTS_LIVE
    assert not planner.advance_if_success(incomplete, progress=progress)

    ready = _state("118")
    ready["lab_timer"] = 0
    ready["inventory"] = ["shield_key", "shotgun"]
    box = [(0, 0)] * BOX_SLOTS_LIVE
    box[0] = (WIND_CREST_ITEM_ID, 1)
    ready["box_cache"] = box
    assert planner.advance_if_success(ready, progress=progress)
    assert planner.current_objective()["checkpoint_id"] == "east_stairs_101_to_yawn"
