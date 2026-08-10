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

**Live policy (2026-08-07):** ``MAGIC_BOX_RAM_WRITES_ENABLED`` is False.
``apply_deposit`` / ``apply_withdraw`` refuse ``write_ram`` while False.
Training transfers go through ``item_box_ui_macro`` (Cross/Triangle) only —
open-box RAM placement also scuffed box/inventory state.
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
# Contiguous RAM through INVENTORY_BASE (see memory_map ITEM_BOX_BASE note).
BOX_SLOTS_LIVE = 48
INVENTORY_SLOTS = 8
LOCKPICK_ITEM_ID = 0x31
KNIFE_ITEM_ID = 0x01
# Knife + healing items only (room-100 bank). Keys/ammo/weapons stay on person.
DEPOSIT_ITEM_ALLOWLIST = frozenset(
    {
        KNIFE_ITEM_ID,
        0x41,  # first_aid_spray_alt
        0x43,  # red_herb
        0x44,  # green_herb
        0x45,  # blue_herb
        0x46,  # mixed_herbs_gr
        0x47,  # mixed_herbs_gg
        0x48,  # mixed_herbs_gb
        0x49,  # mixed_herbs_grb
        0x4A,  # mixed_herbs_ggg
        0x4B,  # mixed_herbs_ggb
    }
)

# Env must leave these False — magic writes scuffed post-cp41 states.
# When False, ``apply_deposit`` / ``apply_withdraw`` are no-ops (no write_ram).
MAGIC_BOX_RAM_WRITES_ENABLED = False
# Policy: deposit UI allowed for knife + healing (``DEPOSIT_ITEM_ALLOWLIST``).
# Live transfers still use ``item_box_ui_macro`` only (no magic RAM writes).
BOX_DEPOSIT_POLICY_ENABLED = True

BOX_ROOMS = frozenset({"100", "118", "30E", "403", "502", "50E", "600", "618"})
# Any known item-box room may deposit allowlisted items (heal / knife bank).
BOX_DEPOSIT_ROOMS = BOX_ROOMS


def _typewriter_or_box_rooms() -> frozenset[str]:
    """Union of RDT typewriter rooms and known item-box rooms."""
    from re1_rl.typewriter_save import TYPEWRITER_ROOMS

    return frozenset(str(r).strip().upper() for r in (BOX_ROOMS | TYPEWRITER_ROOMS))


# Rooms with a typewriter and/or item box. Attack macros are always illegal here
# (do not use empty room_enemies / "safe room" maps — those are untrusted).
TYPEWRITER_OR_BOX_ROOMS: frozenset[str] = _typewriter_or_box_rooms()


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


def read_box_live(bridge: _BridgeReadWrite) -> list[tuple[int, int]]:
    """Full 48-slot box array (UI scroll can park items past index 15)."""
    raw = bridge.read_block(ITEM_BOX_BASE, BOX_SLOTS_LIVE * 2)
    return _decode_block(raw)[:BOX_SLOTS_LIVE]


def read_inventory(bridge: _BridgeReadWrite) -> list[tuple[int, int]]:
    """8 ``(item_id, qty)`` tuples from ``read_block(INVENTORY_BASE, 16)``."""
    raw = bridge.read_block(INVENTORY_BASE, INVENTORY_SLOTS * 2)
    return _decode_block(raw)[:INVENTORY_SLOTS]


def box_pollution_reason(
    box: list[tuple[int, int]] | None,
) -> str | None:
    """Reject key items anywhere in the box, or any item past the modeled 16.

    Sparse UI scroll has parked knife/keys near the inventory boundary (e.g.
    shield_key @ slot 46). Those slots are invisible to BOX_SLOTS=16 reads and
    quality ``-box_ammo``, so polluted cells look clean while story keys vanish.
    """
    from re1_rl.item_todo import canonical_item
    from re1_rl.key_items import KEY_ITEM_NAMES
    from re1_rl.memory_map import ITEM_IDS

    if not box:
        return None
    key_names = frozenset(KEY_ITEM_NAMES)
    for i, entry in enumerate(box):
        if not entry:
            continue
        item_id = int(entry[0])
        if item_id == 0:
            continue
        name = canonical_item(ITEM_IDS.get(item_id, "") or "")
        if name and name in key_names:
            return f"key_item_in_box:{name}@{i}"
        if i >= BOX_SLOTS:
            label = name or f"0x{item_id:02x}"
            return f"deep_box_item:{label}@{i}"
    return None


def can_deposit(
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
    inv_slot: int,
    *,
    enforce_allowlist: bool | None = None,
) -> tuple[bool, str]:
    """Legal iff source occupied, not lockpick, and box has a free empty slot.

    When ``BOX_DEPOSIT_POLICY_ENABLED`` (or ``enforce_allowlist=True``), only
    ``DEPOSIT_ITEM_ALLOWLIST`` ids may deposit (knife + heals).
    """
    if inv_slot < 0 or inv_slot >= len(inventory):
        return False, "bad_slot"
    item_id, qty = inventory[inv_slot]
    if item_id == 0:
        return False, "empty_slot"
    if item_id == LOCKPICK_ITEM_ID:
        return False, "lockpick"
    if effective_transfer_qty(item_id, qty) <= 0:
        return False, "empty_slot"
    use_allowlist = (
        BOX_DEPOSIT_POLICY_ENABLED
        if enforce_allowlist is None
        else bool(enforce_allowlist)
    )
    if use_allowlist and int(item_id) not in DEPOSIT_ITEM_ALLOWLIST:
        return False, "not_allowlisted"
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
    """Validate, plan, write inventory + box; unequip if depositing equipped weapon.

    No-op when ``MAGIC_BOX_RAM_WRITES_ENABLED`` is False (live training).
    """
    if not MAGIC_BOX_RAM_WRITES_ENABLED:
        return {
            "ok": False,
            "reason": "magic_box_ram_writes_disabled",
            "moved": None,
            "unequipped": False,
        }
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
    """Validate, plan, write inventory + box.

    No-op when ``MAGIC_BOX_RAM_WRITES_ENABLED`` is False (live training).
    """
    if not MAGIC_BOX_RAM_WRITES_ENABLED:
        return {
            "ok": False,
            "reason": "magic_box_ram_writes_disabled",
            "moved": None,
            "unequipped": False,
        }
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
    """True when ``room_id`` is a known item-box room."""
    return str(room_id).strip().upper() in BOX_ROOMS


def is_typewriter_or_box_room(room_id: str | None) -> bool:
    """True when ``room_id`` has a typewriter and/or item box."""
    if room_id is None:
        return False
    return str(room_id).strip().upper() in TYPEWRITER_OR_BOX_ROOMS


def sync_inventory_icons_after_knife_ammo_swap(bridge: _BridgeGpu) -> int:
    """Fix stale ITEM-grid icons after magic knife→box / CLIP→inv on QS5 layout.

    Writes a GPURAM delta (see ``inventory_icons``). Call while the ITEM screen
    is open (or immediately before opening it) after ``apply_deposit`` /
    ``apply_withdraw`` have placed CLIP in slot 0. Returns bytes written.
    """
    return apply_gpuram_icon_patch(bridge, PATCH_CLIP_INTO_SLOT0_FROM_KNIFE_QS5)
