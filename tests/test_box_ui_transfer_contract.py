"""L0 transfer-contract tests (plan U1–U10). No BizHawk."""

from __future__ import annotations

from re1_rl.inventory_menu_macro import slot_nav_moves
from re1_rl.item_box import (
    BOX_SLOTS,
    MAGIC_BOX_RAM_WRITES_ENABLED,
    can_deposit,
    can_withdraw,
    is_deposit_allowed_item,
    plan_deposit,
    plan_withdraw,
)
from re1_rl.item_box_ui_macro import (
    HOME_INVENTORY_TAPS,
    _finalize_transfer_failure,
    box_inventory_nav_moves,
    first_empty_inventory_slot,
    transfer_failure_zeros_session_cursors,
    unexpected_keys_lost,
)
from re1_rl.yawn_box_prep_checkpoint import (
    WIND_CREST_ITEM_ID,
    yawn_box_prep_capture_ready,
)

SHIELD_KEY_ID = 0x35
ARMOR_KEY_ID = 0x34
KNIFE_ID = 0x01
BERETTA_ID = 0x02
SHOTGUN_ID = 0x03
BAZOOKA_ACID_ID = 0x07
HANDGUN_BULLETS_ID = 0x0B
SHOTGUN_SHELLS_ID = 0x0C
ACID_ROUNDS_ID = 0x11


def _typical_8pack() -> list[tuple[int, int]]:
    """QS1-style full pack: crest @7, no knife/heal on person."""
    return [
        (BERETTA_ID, 15),
        (HANDGUN_BULLETS_ID, 24),
        (SHIELD_KEY_ID, 1),
        (SHOTGUN_ID, 7),
        (ACID_ROUNDS_ID, 6),
        (ARMOR_KEY_ID, 4),
        (SHOTGUN_SHELLS_ID, 2),
        (WIND_CREST_ITEM_ID, 1),
    ]


def _typical_box() -> list[tuple[int, int]]:
    """Knife @0, bazooka @1, first empty dest @2."""
    box = [(0, 0)] * BOX_SLOTS
    box[0] = (KNIFE_ID, 0)
    box[1] = (BAZOOKA_ACID_ID, 4)
    return box


def _slot_deltas(
    before: list[tuple[int, int]],
    after: list[tuple[int, int]],
) -> list[int]:
    n = max(len(before), len(after))
    changed: list[int] = []
    for i in range(n):
        b = before[i] if i < len(before) else (0, 0)
        a = after[i] if i < len(after) else (0, 0)
        if b != a:
            changed.append(i)
    return changed


def test_u1_118_crest_lost_is_ok() -> None:
    keys_before = {WIND_CREST_ITEM_ID, SHIELD_KEY_ID, ARMOR_KEY_ID}
    keys_after = {SHIELD_KEY_ID, ARMOR_KEY_ID}
    lost = unexpected_keys_lost(
        keys_before, keys_after, WIND_CREST_ITEM_ID, "118"
    )
    assert lost == set()


def test_u2_118_shield_key_lost_still_fails() -> None:
    keys_before = {WIND_CREST_ITEM_ID, SHIELD_KEY_ID, ARMOR_KEY_ID}
    keys_after = {WIND_CREST_ITEM_ID, ARMOR_KEY_ID}
    lost = unexpected_keys_lost(
        keys_before, keys_after, WIND_CREST_ITEM_ID, "118"
    )
    assert lost == {SHIELD_KEY_ID}

    armor_lost = unexpected_keys_lost(
        keys_before,
        {WIND_CREST_ITEM_ID, SHIELD_KEY_ID},
        WIND_CREST_ITEM_ID,
        "118",
    )
    assert armor_lost == {ARMOR_KEY_ID}

    crest_in_100 = unexpected_keys_lost(
        {WIND_CREST_ITEM_ID}, set(), WIND_CREST_ITEM_ID, "100"
    )
    assert crest_in_100 == {WIND_CREST_ITEM_ID}

    both = unexpected_keys_lost(
        keys_before,
        {ARMOR_KEY_ID},
        WIND_CREST_ITEM_ID,
        "118",
    )
    assert both == {SHIELD_KEY_ID}


def test_u3_finalize_failure_sets_exchange_detected() -> None:
    """A GOOD 118 crest deposit must not call this (U1 empty lost).

    ``_finalize_transfer_failure`` still overwrites ``key_item_deposited`` to
    ``exchange_detected`` whenever RAM changed. Env then zeros session cursors.
    """
    assert not MAGIC_BOX_RAM_WRITES_ENABLED

    inv_before = _typical_8pack()
    inv_after = list(inv_before)
    inv_after[7] = (0, 0)
    box_before = _typical_box()
    box_after = list(box_before)
    box_after[2] = (WIND_CREST_ITEM_ID, 1)

    keys_before = {WIND_CREST_ITEM_ID, SHIELD_KEY_ID, ARMOR_KEY_ID}
    keys_after = {SHIELD_KEY_ID, ARMOR_KEY_ID}
    assert unexpected_keys_lost(
        keys_before, keys_after, WIND_CREST_ITEM_ID, "118"
    ) == set()

    report: dict = {
        "ok": False,
        "inv_cursor": 7,
        "box_cursor": 2,
        "room_id": "118",
    }
    _finalize_transfer_failure(
        report,
        inv_before=inv_before,
        inv_after=inv_after,
        box_before=box_before,
        box_after=box_after,
        box_live_before=box_before,
        box_live_after=box_after,
        default_reason="key_item_deposited",
        room_id="118",
    )
    assert report["ram_changed"] is True
    assert report["reason"] == "exchange_detected"
    assert report.get("exchange_detected") is True
    assert "inv_cursor" not in report
    assert "box_cursor" not in report
    assert transfer_failure_zeros_session_cursors(report) is True
    assert transfer_failure_zeros_session_cursors({"ok": True, "ram_changed": True}) is False
    assert transfer_failure_zeros_session_cursors({"ok": False}) is False


def test_u4_deposit_allowlist_crest_and_keys() -> None:
    assert is_deposit_allowed_item(WIND_CREST_ITEM_ID, "118")
    assert not is_deposit_allowed_item(WIND_CREST_ITEM_ID, "100")
    assert not is_deposit_allowed_item(SHIELD_KEY_ID, "118")
    assert not is_deposit_allowed_item(SHIELD_KEY_ID, "100")
    assert not is_deposit_allowed_item(ARMOR_KEY_ID, "118")


def test_u5_118_full_pack_only_crest_depositable() -> None:
    inv = _typical_8pack()
    box = _typical_box()
    illegal = {
        0: BERETTA_ID,
        1: HANDGUN_BULLETS_ID,
        2: SHIELD_KEY_ID,
        3: SHOTGUN_ID,
        4: ACID_ROUNDS_ID,
        5: ARMOR_KEY_ID,
        6: SHOTGUN_SHELLS_ID,
    }
    for slot, iid in illegal.items():
        assert not is_deposit_allowed_item(iid, "118")
        ok, reason = can_deposit(inv, box, slot, room_id="118", enforce_allowlist=True)
        assert not ok, f"slot {slot} id=0x{iid:02x} should be illegal"
        assert reason in {"not_allowlisted", "key_item"}
    ok7, why7 = can_deposit(inv, box, 7, room_id="118", enforce_allowlist=True)
    assert ok7 and why7 == ""


def test_u6_withdraw_full_pack_then_every_box_slot_after_hole() -> None:
    inv = _typical_8pack()
    box = _typical_box()
    for slot in (0, 1):
        ok, reason = can_withdraw(inv, box, slot)
        assert not ok and reason == "inventory_full"

    holed, boxed, _ = plan_deposit(inv, box, 7)
    dest = first_empty_inventory_slot(holed)
    assert dest == 7
    assert boxed[2][0] == WIND_CREST_ITEM_ID
    for slot, iid in ((0, KNIFE_ID), (1, BAZOOKA_ACID_ID), (2, WIND_CREST_ITEM_ID)):
        ok, reason = can_withdraw(holed, boxed, slot)
        assert ok and reason == "", f"box[{slot}] should be withdraw-legal"
        new_box, new_inv, moved = plan_withdraw(holed, boxed, slot)
        assert moved > 0
        assert new_inv[int(dest)][0] == iid
        assert _slot_deltas(holed, new_inv) == [dest]
        assert _slot_deltas(boxed, new_box) == [slot]
        for i, pair in enumerate(holed):
            if i != dest:
                assert new_inv[i] == pair
        for i, pair in enumerate(boxed):
            if i != slot:
                assert new_box[i] == pair


def test_u7_plan_deposit_withdraw_ram_identity() -> None:
    inv = _typical_8pack()
    box = _typical_box()
    new_inv, new_box, moved = plan_deposit(inv, box, 7)
    assert moved == 1
    assert new_inv[7] == (0, 0)
    assert new_box[2] == (WIND_CREST_ITEM_ID, 1)
    assert _slot_deltas(inv, new_inv) == [7]
    assert _slot_deltas(box, new_box) == [2]
    assert new_inv[2] == (SHIELD_KEY_ID, 1)
    assert new_box[0] == (KNIFE_ID, 0)
    assert new_box[1] == (BAZOOKA_ACID_ID, 4)

    dest = first_empty_inventory_slot(new_inv)
    assert dest == 7
    out_box, out_inv, wmoved = plan_withdraw(new_inv, new_box, 1)
    assert wmoved > 0
    assert out_inv[7][0] == BAZOOKA_ACID_ID
    assert out_box[2] == (WIND_CREST_ITEM_ID, 1)
    assert out_inv[2] == (SHIELD_KEY_ID, 1)
    assert out_box[0] == (KNIFE_ID, 0)
    assert _slot_deltas(new_inv, out_inv) == [7]
    assert _slot_deltas(new_box, out_box) == [1]


def test_u8_nav_0_to_7_matches_slot_nav() -> None:
    full = _typical_8pack()
    expected = ["down", "down", "down", "right"]
    assert slot_nav_moves(0, 7) == expected
    assert box_inventory_nav_moves(0, 7, full) == expected
    assert box_inventory_nav_moves(0, 7, full) == slot_nav_moves(0, 7)


def test_u9_home_inventory_vertical_only_odd_column_misses_slot_0() -> None:
    """``_home_inventory`` is up×3 only. No Left — odd columns stay on slot 1.

    Cold deposit must not call it: live D1 Up from slot 0 hits EXIT, then the
    assumed 0→7 path deposits bullets@1. Odd start 1/3/5/7 still cannot reach
    slot 0 until live C9 adds Left (or close+reopen).
    """
    assert HOME_INVENTORY_TAPS == (("up", 3),)
    moves = [d for d, n in HOME_INVENTORY_TAPS for _ in range(n)]
    assert moves == ["up", "up", "up"]
    assert "left" not in moves and "right" not in moves and "down" not in moves
    for odd in (1, 3, 5, 7):
        landing_col = odd % 2
        assert landing_col == 1
        assert landing_col != 0


def test_u10_7pack_withdraw_legal_before_deposit_capture_still_needs_crest() -> None:
    inv = [
        (BERETTA_ID, 15),
        (SHIELD_KEY_ID, 1),
        (SHOTGUN_ID, 7),
        (ACID_ROUNDS_ID, 6),
        (ARMOR_KEY_ID, 4),
        (SHOTGUN_SHELLS_ID, 2),
        (WIND_CREST_ITEM_ID, 1),
        (0, 0),
    ]
    box = _typical_box()
    ok_wd, why_wd = can_withdraw(inv, box, 1)
    assert ok_wd and why_wd == ""
    ok_dep, why_dep = can_deposit(inv, box, 6, room_id="118", enforce_allowlist=True)
    assert ok_dep and why_dep == ""

    inv_names = [
        "beretta",
        "shield_key",
        "shotgun",
        "acid_rounds",
        "armor_key",
        "shotgun_shells",
        "wind_crest",
    ]
    assert yawn_box_prep_capture_ready(box, inv_names) == (
        "yawn_box_weapon_ammo:bazooka_acid@1"
    )

    _banked_inv, banked_box, _ = plan_deposit(inv, box, 6)
    names_after_bank = [n for n in inv_names if n != "wind_crest"]
    assert yawn_box_prep_capture_ready(banked_box, names_after_bank) == (
        "yawn_box_weapon_ammo:bazooka_acid@1"
    )

    ready_box = list(banked_box)
    ready_box[1] = (0, 0)
    assert yawn_box_prep_capture_ready(ready_box, names_after_bank) is None
    assert yawn_box_prep_capture_ready(ready_box, inv_names) == "wind_crest_still_held"
    assert yawn_box_prep_capture_ready(_typical_box(), names_after_bank) == (
        "yawn_box_weapon_ammo:bazooka_acid@1"
    )
