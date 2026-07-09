"""Offline tests for item-box deposit / withdraw logic (no emulator)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.item_box import (  # noqa: E402
    BOX_SLOTS,
    INVENTORY_SLOTS,
    LOCKPICK_ITEM_ID,
    apply_deposit,
    apply_withdraw,
    can_deposit,
    can_withdraw,
    is_box_room,
    plan_deposit,
    plan_withdraw,
)
from re1_rl.memory_map import (  # noqa: E402
    EQUIPPED_WEAPON_ID,
    EQUIPPED_SLOT_INDEX_1BASED,
    INVENTORY_BASE,
    ITEM_BOX_BASE,
)


def _empty_inventory() -> list[tuple[int, int]]:
    return [(0, 0)] * INVENTORY_SLOTS


def _empty_box() -> list[tuple[int, int]]:
    return [(0, 0)] * BOX_SLOTS


def _block_bytes(slots: list[tuple[int, int]]) -> list[int]:
    out: list[int] = []
    for item_id, qty in slots:
        out.extend([item_id, qty])
    return out


class FakeBridge:
    """Records ``write_ram``; serves canned ``read_block`` bytes keyed by address."""

    def __init__(
        self,
        *,
        inventory: list[tuple[int, int]] | None = None,
        box: list[tuple[int, int]] | None = None,
    ) -> None:
        self.blocks: dict[int, list[int]] = {
            INVENTORY_BASE: _block_bytes(inventory or _empty_inventory()),
            ITEM_BOX_BASE: _block_bytes(box or _empty_box()),
        }
        self.writes: list[list[tuple[str, int, str, int]]] = []

    def read_block(self, address: int, count: int) -> list[int]:
        data = self.blocks.get(address, [0] * count)
        return list(data[:count])

    def write_ram(self, fields: list[tuple[str, int, str, int]]) -> None:
        self.writes.append(list(fields))
        for _name, addr, dtype, value in fields:
            if dtype == "u16":
                item_id = int(value) & 0xFF
                qty = (int(value) >> 8) & 0xFF
                off = addr - INVENTORY_BASE if addr >= INVENTORY_BASE else addr - ITEM_BOX_BASE
                base = INVENTORY_BASE if addr >= INVENTORY_BASE else ITEM_BOX_BASE
                blk = self.blocks.setdefault(base, [])
                while len(blk) < off + 2:
                    blk.extend([0, 0])
                blk[off] = item_id
                blk[off + 1] = qty


def test_deposit_happy_path():
    inv = [(0x02, 15), (0x01, 1)] + [(0, 0)] * 6
    box = _empty_box()
    ok, reason = can_deposit(inv, box, 0)
    assert ok and reason == ""

    new_inv, new_box, moved = plan_deposit(inv, box, 0)
    assert moved == 15
    assert new_inv[0] == (0, 0)
    assert new_inv[1] == (0x01, 1)
    assert new_box[0] == (0x02, 15)

    bridge = FakeBridge(inventory=inv, box=box)
    result = apply_deposit(bridge, 0, equipped_weapon_id=0x01)
    assert result == {
        "ok": True,
        "reason": "",
        "moved": (0x02, 15),
        "unequipped": False,
    }
    assert len(bridge.writes) == 1


def test_deposit_lockpick_refused():
    inv = [(LOCKPICK_ITEM_ID, 1)] + [(0, 0)] * 7
    box = _empty_box()
    ok, reason = can_deposit(inv, box, 0)
    assert not ok and reason == "lockpick"

    bridge = FakeBridge(inventory=inv, box=box)
    result = apply_deposit(bridge, 0, equipped_weapon_id=0)
    assert result["ok"] is False and result["reason"] == "lockpick"
    assert bridge.writes == []


def test_deposit_from_empty_slot_refused():
    inv = _empty_inventory()
    box = _empty_box()
    ok, reason = can_deposit(inv, box, 2)
    assert not ok and reason == "empty_slot"


def test_deposit_with_full_box_refused_when_no_merge():
    inv = [(0x02, 15)] + [(0, 0)] * 7
    box = [(0x0B, 1)] * BOX_SLOTS
    ok, reason = can_deposit(inv, box, 0)
    assert not ok and reason == "box_full"


def test_deposit_merges_when_box_has_no_empty_slot():
    inv = [(0x02, 10)] + [(0, 0)] * 7
    box = [(0x0B, 1)] * BOX_SLOTS
    box[0] = (0x02, 50)
    ok, reason = can_deposit(inv, box, 0)
    assert ok and reason == ""
    new_inv, new_box, moved = plan_deposit(inv, box, 0)
    assert moved == 10
    assert new_box[0] == (0x02, 60)
    assert new_inv[0] == (0, 0)


def test_withdraw_merges_handgun_partial_overflow():
    inv = [(0x02, 50)] + [(0, 0)] * 7
    box = [(0x02, 15)] + [(0, 0)] * (BOX_SLOTS - 1)
    ok, _ = can_withdraw(inv, box, 0)
    assert ok
    new_box, new_inv, moved = plan_withdraw(inv, box, 0)
    assert moved == 10
    assert new_inv[0] == (0x02, 60)
    assert new_box[0] == (0x02, 5)


def test_withdraw_to_full_inventory_allowed_when_merge_fits():
    inv = [(0x01, 1)] * INVENTORY_SLOTS
    inv[0] = (0x02, 50)
    box = [(0x02, 15)] + [(0, 0)] * (BOX_SLOTS - 1)
    ok, reason = can_withdraw(inv, box, 0)
    assert ok and reason == ""


def test_apply_partial_deposit_does_not_unequip():
    inv = [(0x02, 50)] + [(0, 0)] * 7
    box = [(0x02, 50)] + [(0, 0)] * (BOX_SLOTS - 1)
    bridge = FakeBridge(inventory=inv, box=box)
    result = apply_deposit(bridge, 0, equipped_weapon_id=0x02)
    assert result["ok"] is True
    assert result["moved"] == (0x02, 10)
    assert result["unequipped"] is False


def test_withdraw_happy_path():
    inv = [(0x01, 1)] + [(0, 0)] * 7
    box = [(0x0C, 5)] + [(0, 0)] * (BOX_SLOTS - 1)
    ok, reason = can_withdraw(inv, box, 0)
    assert ok and reason == ""

    new_box, new_inv, moved = plan_withdraw(inv, box, 0)
    assert moved == 5
    assert new_inv[0] == (0x01, 1)
    assert new_inv[1] == (0x0C, 5)
    assert new_box[0] == (0, 0)

    bridge = FakeBridge(inventory=inv, box=box)
    result = apply_withdraw(bridge, 0)
    assert result == {
        "ok": True,
        "reason": "",
        "moved": (0x0C, 5),
        "unequipped": False,
    }


def test_withdraw_to_full_inventory_refused():
    inv = [(0x01, 1)] * INVENTORY_SLOTS
    box = [(0x03, 5)] + [(0, 0)] * (BOX_SLOTS - 1)
    ok, reason = can_withdraw(inv, box, 0)
    assert not ok and reason == "inventory_full"


def test_withdraw_empty_box_slot_refused():
    inv = _empty_inventory()
    box = _empty_box()
    ok, reason = can_withdraw(inv, box, 3)
    assert not ok and reason == "empty_slot"


def test_plan_functions_leave_gaps_no_compaction():
    inv = [(0x01, 1), (0, 0), (0x02, 15), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0)]
    box = [(0, 0), (0x0B, 1)] + [(0, 0)] * (BOX_SLOTS - 2)

    new_inv, new_box, moved = plan_deposit(inv, box, 2)
    assert moved == 15
    assert new_inv[2] == (0, 0)
    assert new_inv[0] == (0x01, 1)
    assert new_box[0] == (0x02, 15)
    assert new_box[1] == (0x0B, 1)

    inv2 = [(0x01, 1), (0, 0), (0, 0), (0x04, 1), (0, 0), (0, 0), (0, 0), (0, 0)]
    box2 = [(0x0C, 5), (0, 0)] + [(0, 0)] * (BOX_SLOTS - 2)
    new_box2, new_inv2, moved2 = plan_withdraw(inv2, box2, 0)
    assert moved2 == 5
    assert new_inv2[1] == (0x0C, 5)
    assert new_inv2[0] == (0x01, 1)
    assert new_inv2[3] == (0x04, 1)
    assert new_box2[0] == (0, 0)


def test_apply_deposit_unequips_equipped_weapon():
    inv = [(0x02, 15)] + [(0, 0)] * 7
    box = _empty_box()
    bridge = FakeBridge(inventory=inv, box=box)
    result = apply_deposit(bridge, 0, equipped_weapon_id=0x02)
    assert result["ok"] is True
    assert result["unequipped"] is True
    fields = bridge.writes[-1]
    unequip = {
        (addr, val)
        for _n, addr, dtype, val in fields
        if dtype == "u8"
        and addr in (EQUIPPED_WEAPON_ID, EQUIPPED_SLOT_INDEX_1BASED)
    }
    assert unequip == {(EQUIPPED_WEAPON_ID, 0), (EQUIPPED_SLOT_INDEX_1BASED, 0)}


def test_apply_deposit_does_not_unequip_other_item():
    inv = [(0x0B, 1)] + [(0, 0)] * 7
    box = _empty_box()
    bridge = FakeBridge(inventory=inv, box=box)
    result = apply_deposit(bridge, 0, equipped_weapon_id=0x02)
    assert result["ok"] is True
    assert result["unequipped"] is False
    fields = bridge.writes[-1]
    assert not any(
        addr in (EQUIPPED_WEAPON_ID, EQUIPPED_SLOT_INDEX_1BASED)
        for _n, addr, _dt, _val in fields
    )


def test_is_box_room():
    assert is_box_room("100")
    assert is_box_room("30e")
    assert not is_box_room("105")
    assert not is_box_room("11B")


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
