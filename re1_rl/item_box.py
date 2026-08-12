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
INK_RIBBON_ITEM_ID = 0x2F
# Knife + healing + ink ribbons (global bank). Keys/ammo/weapons stay on person
# except room-100 bazooka banking below.
DEPOSIT_ITEM_ALLOWLIST = frozenset(
    {
        KNIFE_ITEM_ID,
        INK_RIBBON_ITEM_ID,
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
# Mansion Save Room (100): bank grenade/rocket launchers + their round packs.
BAZOOKA_WEAPON_IDS = frozenset(
    {
        0x07,  # bazooka_acid
        0x08,  # bazooka_explosive
        0x09,  # bazooka_flame
        0x0A,  # rocket_launcher
    }
)
BAZOOKA_AMMO_IDS = frozenset(
    {
        0x10,  # explosive_rounds
        0x11,  # acid_rounds
        0x12,  # flame_rounds
    }
)
ROOM_100_EXTRA_DEPOSIT_IDS = BAZOOKA_WEAPON_IDS | BAZOOKA_AMMO_IDS
BOX_BAZOOKA_DEPOSIT_ROOMS = frozenset({"100"})
# Room 118 yawn prep: ammo may be banked (then withdrawn) while rearranging
# the pack so wind_crest can sit in the box before leaving.
ROOM_118_AMMO_DEPOSIT_IDS = frozenset(
    {
        0x0B,  # handgun_bullets
        0x0C,  # shotgun_shells
        0x0D,  # dumdum_rounds
        0x0E,  # magnum_rounds
        0x0F,  # flamethrower_fuel
    }
) | BAZOOKA_AMMO_IDS

# Env must leave these False — magic writes scuffed post-cp41 states.
# When False, ``apply_deposit`` / ``apply_withdraw`` are no-ops (no write_ram).
MAGIC_BOX_RAM_WRITES_ENABLED = False
# Policy: deposit UI allowed for knife + healing (``DEPOSIT_ITEM_ALLOWLIST``).
# Live transfers still use ``item_box_ui_macro`` only (no magic RAM writes).
BOX_DEPOSIT_POLICY_ENABLED = True

BOX_ROOMS = frozenset({"100", "118", "30E", "403", "502", "50E", "600", "618"})
# Any known item-box room may deposit allowlisted items (heal / knife bank).
BOX_DEPOSIT_ROOMS = BOX_ROOMS

# Knife/heals (deposit bank) plus ammo stacks normally kept in the box,
# plus room-100 bazooka bank items (weapons + packs).
BOX_STORABLE_ITEM_IDS = DEPOSIT_ITEM_ALLOWLIST | ROOM_100_EXTRA_DEPOSIT_IDS | frozenset(
    {
        0x0B,  # handgun_bullets
        0x0C,  # shotgun_shells
        0x0D,  # dumdum_rounds
        0x0E,  # magnum_rounds
        0x0F,  # flamethrower_fuel
    }
)


def deposit_allowlist_for_room(room_id: str | None) -> frozenset[int]:
    """Base knife/heal bank; room 100 also allows bazooka variants + packs."""
    from re1_rl.yawn_box_prep_checkpoint import WIND_CREST_ITEM_ID, YAWN_BOX_PREP_ROOM

    rid = str(room_id or "").strip().upper()
    if rid in BOX_BAZOOKA_DEPOSIT_ROOMS:
        return DEPOSIT_ITEM_ALLOWLIST | ROOM_100_EXTRA_DEPOSIT_IDS
    if rid == YAWN_BOX_PREP_ROOM:
        return DEPOSIT_ITEM_ALLOWLIST | {WIND_CREST_ITEM_ID} | ROOM_118_AMMO_DEPOSIT_IDS
    return DEPOSIT_ITEM_ALLOWLIST


def is_key_item_id(item_id: int) -> bool:
    """True for story keys (chemical, crest, shield_key, …). Never depositable."""
    from re1_rl.item_todo import canonical_item
    from re1_rl.key_items import KEY_ITEM_NAMES
    from re1_rl.memory_map import ITEM_IDS

    name = canonical_item(ITEM_IDS.get(int(item_id) & 0xFF, "") or "")
    return bool(name) and name in KEY_ITEM_NAMES


def is_deposit_allowed_item(item_id: int, room_id: str | None = None) -> bool:
    """Allowlist gate used by masks + macros (keys always False)."""
    from re1_rl.yawn_box_prep_checkpoint import wind_crest_deposit_allowed

    iid = int(item_id) & 0xFF
    if wind_crest_deposit_allowed(iid, room_id):
        return True
    if iid == 0 or is_key_item_id(iid):
        return False
    return iid in deposit_allowlist_for_room(room_id)


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
    *,
    room_id: str | None = None,
) -> str | None:
    """Reject illegal box contents (keys, weapons, deep scroll, non-bank items).

    Sparse UI scroll has parked knife/keys near the inventory boundary (e.g.
    shield_key @ slot 46). Those slots are invisible to BOX_SLOTS=16 reads and
    quality ``-box_ammo``, so polluted cells look clean while story keys vanish.

    Modeled slots 0–15 may hold knife/heals/ammo and room-100 bazooka bank
    items; beretta and other non-bank weapons trigger ``disallowed_item_in_box``
    (including qty-0 ghosts).
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
            if name == "wind_crest":
                continue
            return f"key_item_in_box:{name}@{i}"
        if i >= BOX_SLOTS:
            label = name or f"0x{item_id:02x}"
            return f"deep_box_item:{label}@{i}"
        if int(item_id) not in BOX_STORABLE_ITEM_IDS:
            label = name or f"0x{item_id:02x}"
            return f"disallowed_item_in_box:{label}@{i}"
    return None


def can_deposit(
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
    inv_slot: int,
    *,
    room_id: str | None = None,
    enforce_allowlist: bool | None = None,
) -> tuple[bool, str]:
    """Legal iff source occupied, allowlisted, and modeled box has a free slot.

    Key items (chemical, crests, …) are **always** refused — not policy-gated.
    Destination is the first empty slot among the modeled 16 only; deep UI
    scroll slots (16–47) are invisible to the NN and must never be targets.
    """
    if inv_slot < 0 or inv_slot >= len(inventory):
        return False, "bad_slot"
    item_id, qty = inventory[inv_slot]
    if item_id == 0:
        return False, "empty_slot"
    if item_id == LOCKPICK_ITEM_ID:
        return False, "lockpick"
    from re1_rl.yawn_box_prep_checkpoint import wind_crest_deposit_allowed

    if is_key_item_id(int(item_id)) and not wind_crest_deposit_allowed(
        int(item_id), room_id
    ):
        return False, "key_item"
    if effective_transfer_qty(item_id, qty) <= 0:
        return False, "empty_slot"
    use_allowlist = (
        BOX_DEPOSIT_POLICY_ENABLED
        if enforce_allowlist is None
        else bool(enforce_allowlist)
    )
    # Live macros always pass enforce_allowlist=True. Keys already refused above.
    if use_allowlist and int(item_id) not in deposit_allowlist_for_room(room_id):
        return False, "not_allowlisted"
    modeled = list(box)[:BOX_SLOTS]
    if _first_empty_slot(modeled) is None:
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
    room_id: str | None = None,
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
    ok, reason = can_deposit(inventory, box, inv_slot, room_id=room_id)
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


def write_inventory_box_curation(
    bridge: _BridgeReadWrite,
    inventory: list[tuple[int, int]],
    box: list[tuple[int, int]],
) -> None:
    """Curation-only RAM write for cell repair scripts (not live training)."""
    inv = list(inventory)[:INVENTORY_SLOTS]
    bx = list(box)[:BOX_SLOTS]
    while len(inv) < INVENTORY_SLOTS:
        inv.append((0, 0))
    while len(bx) < BOX_SLOTS:
        bx.append((0, 0))
    fields = _slot_write_fields("inv", INVENTORY_BASE, inv)
    fields.extend(_slot_write_fields("box", ITEM_BOX_BASE, bx))
    bridge.write_ram(fields)


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
