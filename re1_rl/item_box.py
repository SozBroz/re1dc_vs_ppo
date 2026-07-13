"""Pure logic for PS1 RE1 item-box deposit / withdraw (magic RAM transfers).

Item box: ``ITEM_BOX_BASE`` @ 0x800C8724, 16 slots × 2 bytes (item_id, qty).
Inventory: ``INVENTORY_BASE`` @ 0x800C8784, 8 slots × 2 bytes (Jill).
Equipped weapon: ``EQUIPPED_WEAPON_ID`` @ 0x800C5126 (item id) and
``EQUIPPED_SLOT_INDEX_1BASED`` @ 0x800C8689 (1-based slot; 0 = none).

Deposit always targets the **first empty** box slot (never merges into an
existing stack, never overwrites an occupied slot). Withdraw may still merge
into on-person stacks (see ``inventory_stacking``).

NOTE (deferred 2026-07-12): the live box array appears to be **48** slots
(contiguous up to ``INVENTORY_BASE``); UI scroll can place items past index 15.
``BOX_SLOTS`` stays 16 until withdraw action space is widened — deposits only
touch the modeled 16 and must not clobber later RAM.

ITEM-screen grid icons are separate (BizHawk ``GPURAM``). After magic swaps
that change which art a slot should show, call
``sync_inventory_icons_after_knife_ammo_swap`` (or apply a GPURAM patch from
``inventory_icons``) while the ITEM UI is open.
"""

from __future__ import annotations

from typing import Any, Protocol

from re1_rl.inventory_icons import (
    PATCH_CLIP_INTO_SLOT0_FROM_KNIFE_QS5,
    apply_gpuram_icon_patch,
)
from re1_rl.inventory_stacking import (
    apply_stack_transfer,
    effective_transfer_qty,
    max_transferable,
    stack_limit,
)
from re1_rl.memory_map import (
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    INVENTORY_BASE,
    ITEM_BOX_BASE,
)

BOX_SLOTS = 16
INVENTORY_SLOTS = 8
LOCKPICK_ITEM_ID = 0x31

BOX_ROOMS = frozenset({"100", "118", "30E", "403", "502", "50E", "600", "618"})


class _BridgeReadWrite(Protocol):
    def read_block(self, address: int, count: int) -> list[int]: ...

    def write_ram(self, fields: list[tuple[str, int, str, int]]) -> None: ...


class _BridgeGpu(Protocol):
    def write_domain(self, domain: str, address: int, data: bytes | list[int]) -> None: ...


def _encode_slot(item_id: int, qty: int) -> int:
    """u16 LE: low byte = item_id, high byte = qty."""
    return ((int(qty) & 0xFF) << 8) | (int(item_id) & 0xFF)


def _decode_block(raw: list[int]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for i in range(0, len(raw), 2):
        item_id = int(raw[i])
        qty = int(raw[i + 1]) if i + 1 < len(raw) else 0
        pairs.append((item_id, qty))
    return pairs


def _slot_write_fields(
    prefix: str,
    base: int,
    slots: list[tuple[int, int]],
) -> list[tuple[str, int, str, int]]:
    return [
        (f"{prefix}_{i}", base + i * 2, "u16", _encode_slot(item_id, qty))
        for i, (item_id, qty) in enumerate(slots)
    ]


def _first_empty_slot(slots: list[tuple[int, int]]) -> int | None:
    for i, (item_id, _qty) in enumerate(slots):
        if item_id == 0:
            return i
    return None


def read_box(bridge: _BridgeReadWrite) -> list[tuple[int, int]]:
    """16 ``(item_id, qty)`` tuples from ``read_block(ITEM_BOX_BASE, 32)``."""
    raw = bridge.read_block(ITEM_BOX_BASE, BOX_SLOTS * 2)
    return _decode_block(raw)[:BOX_SLOTS]


def read_inventory(bridge: _BridgeReadWrite) -> list[tuple[int, int]]:
    """8 ``(item_id, qty)`` tuples from ``read_block(INVENTORY_BASE, 16)``."""
    raw = bridge.read_block(INVENTORY_BASE, INVENTORY_SLOTS * 2)
    return _decode_block(raw)[:INVENTORY_SLOTS]


def can_deposit(
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
    inv_slot: int,
) -> tuple[bool, str]:
    """Legal iff source occupied, not lockpick, and box has a free empty slot."""
    if inv_slot < 0 or inv_slot >= len(inventory):
        return False, "bad_slot"
    item_id, qty = inventory[inv_slot]
    if item_id == 0:
        return False, "empty_slot"
    if item_id == LOCKPICK_ITEM_ID:
        return False, "lockpick"
    if effective_transfer_qty(item_id, qty) <= 0:
        return False, "empty_slot"
    if _first_empty_slot(box) is None:
        return False, "box_full"
    return True, ""


def can_withdraw(
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
    box_slot: int,
) -> tuple[bool, str]:
    """Legal iff box slot occupied and inventory can accept >=1 unit."""
    if box_slot < 0 or box_slot >= len(box):
        return False, "bad_slot"
    item_id, qty = box[box_slot]
    if item_id == 0:
        return False, "empty_slot"
    if max_transferable(inventory, item_id, qty) <= 0:
        return False, "inventory_full"
    return True, ""


def plan_deposit(
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
    inv_slot: int,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], int]:
    """Deposit ``inv_slot`` into the first empty box slot only.

    Never merges into an existing box stack and never overwrites an occupied
    slot. Returns ``(new_inv, new_box, moved_qty)``.
    """
    new_inv = list(inventory)
    new_box = list(box)
    if inv_slot < 0 or inv_slot >= len(new_inv):
        return new_inv, new_box, 0

    item_id, qty = new_inv[inv_slot]
    avail = effective_transfer_qty(item_id, qty)
    if avail <= 0:
        return new_inv, new_box, 0

    empty = _first_empty_slot(new_box)
    if empty is None:
        return new_inv, new_box, 0

    before_occupied = {
        i: new_box[i] for i in range(len(new_box)) if new_box[i][0] != 0
    }

    limit = stack_limit(item_id)
    moved = min(avail, limit)
    # Preserve knife/empty-weapon RAM qty 0 when moving the whole slot.
    write_qty = moved if int(qty) > 0 else int(qty)
    new_box[empty] = (item_id, write_qty)

    remaining = avail - moved
    if remaining > 0:
        new_inv[inv_slot] = (item_id, remaining if int(qty) > 0 else 0)
    else:
        new_inv[inv_slot] = (0, 0)

    for i, pair in before_occupied.items():
        if i == empty:
            continue
        assert new_box[i] == pair, f"deposit clobbered box slot {i}"

    return new_inv, new_box, moved


def plan_withdraw(
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
    box_slot: int,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], int]:
    """Withdraw from ``box_slot`` into inventory (merge-aware). Returns ``moved_qty``."""
    new_box, new_inv, moved = apply_stack_transfer(box, inventory, box_slot)
    return new_box, new_inv, moved


def apply_deposit(
    bridge: _BridgeReadWrite,
    inv_slot: int,
    *,
    equipped_weapon_id: int,
) -> dict[str, Any]:
    """Validate, plan, write inventory + box; unequip if depositing equipped weapon."""
    inventory = read_inventory(bridge)
    box = read_box(bridge)
    ok, reason = can_deposit(inventory, box, inv_slot)
    if not ok:
        return {"ok": False, "reason": reason, "moved": None, "unequipped": False}

    item_id, _qty = inventory[inv_slot]
    new_inv, new_box, moved = plan_deposit(inventory, box, inv_slot)
    if moved <= 0:
        return {"ok": False, "reason": "box_full", "moved": None, "unequipped": False}

    fields = _slot_write_fields("inv", INVENTORY_BASE, new_inv)
    fields.extend(_slot_write_fields("box", ITEM_BOX_BASE, new_box))

    slot_cleared = new_inv[inv_slot] == (0, 0)
    unequipped = (
        slot_cleared
        and int(item_id) == int(equipped_weapon_id)
        and int(equipped_weapon_id) != 0
    )
    if unequipped:
        fields.append(("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8", 0))
        fields.append(("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8", 0))

    bridge.write_ram(fields)
    return {
        "ok": True,
        "reason": "",
        "moved": (item_id, moved),
        "unequipped": unequipped,
    }


def apply_withdraw(bridge: _BridgeReadWrite, box_slot: int) -> dict[str, Any]:
    """Validate, plan, write inventory + box."""
    inventory = read_inventory(bridge)
    box = read_box(bridge)
    ok, reason = can_withdraw(inventory, box, box_slot)
    if not ok:
        return {"ok": False, "reason": reason, "moved": None, "unequipped": False}

    item_id, _qty = box[box_slot]
    new_box, new_inv, moved = plan_withdraw(inventory, box, box_slot)
    if moved <= 0:
        return {
            "ok": False,
            "reason": "inventory_full",
            "moved": None,
            "unequipped": False,
        }

    fields = _slot_write_fields("inv", INVENTORY_BASE, new_inv)
    fields.extend(_slot_write_fields("box", ITEM_BOX_BASE, new_box))
    bridge.write_ram(fields)
    return {
        "ok": True,
        "reason": "",
        "moved": (item_id, moved),
        "unequipped": False,
    }


def is_box_room(room_id: str) -> bool:
    """True when ``room_id`` is a known item-box room (e.g. ``'100'``, ``'11B'`` excluded)."""
    return str(room_id).strip().upper() in BOX_ROOMS


def sync_inventory_icons_after_knife_ammo_swap(bridge: _BridgeGpu) -> int:
    """Fix stale ITEM-grid icons after magic knife→box / CLIP→inv on QS5 layout.

    Writes a GPURAM delta (see ``inventory_icons``). Call while the ITEM screen
    is open (or immediately before opening it) after ``apply_deposit`` /
    ``apply_withdraw`` have placed CLIP in slot 0. Returns bytes written.
    """
    return apply_gpuram_icon_patch(bridge, PATCH_CLIP_INTO_SLOT0_FROM_KNIFE_QS5)
