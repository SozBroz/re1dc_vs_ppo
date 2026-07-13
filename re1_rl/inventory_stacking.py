"""RE1 Jill inventory / item-box stack limits and merge transfers.

PS1 Director's Cut RAM uses (item_id, qty) per slot. Handgun spare ammo is
``handgun_bullets`` (0x0B); the beretta slot (0x02) holds loaded clip qty.
Separate ammo ids (``shotgun_shells``, etc.) stack on themselves. Unique /
health / weapon items do not merge (limit 1).

Limits are derived from RE1 PC HD exe patches (kTeo, residentevilmodding
thread 13857) with PS1 handgun cap 60 (Rebirth item-box seed + in-game
clip stacking). Herb *combination* (green+red -> mixed) is a menu action,
not automatic stack merge — herbs stay non-stackable here.
"""

from __future__ import annotations

from re1_rl.memory_map import WEAPON_ITEM_IDS

# Stackable ammo / fuel: max qty per slot (u8-clamped).
STACK_LIMITS: dict[int, int] = {
    0x02: 15,  # beretta loaded clip (PS1 DC magazine)
    0x0B: 60,  # spare handgun_bullets pile
    0x0C: 15,  # shotgun shells (PC HD)
    0x0D: 10,  # dumdum rounds (PC HD handcannon)
    0x0E: 10,  # magnum rounds (PC HD)
    0x0F: 255,  # flamethrower fuel (byte max; clips are large)
    0x10: 1,  # explosive rounds (bazooka / launcher)
    0x11: 1,  # acid rounds
    0x12: 1,  # flame rounds
}

# Health / spray / mixed herbs — one per slot (RE1 DC combine menu).
HEALTH_ITEM_IDS = frozenset({
    0x41, 0x42,
    0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B,
})

# Knife uses qty 0; still one per slot.
NON_STACKABLE_WEAPON_IDS = frozenset(WEAPON_ITEM_IDS) - {0x02}


def stack_limit(item_id: int) -> int:
    """Max qty for one slot holding ``item_id`` (minimum 1)."""
    iid = int(item_id) & 0xFF
    if iid == 0:
        return 0
    if iid in STACK_LIMITS:
        return STACK_LIMITS[iid]
    if iid in HEALTH_ITEM_IDS or iid in NON_STACKABLE_WEAPON_IDS:
        return 1
    return 1


def is_stackable(item_id: int) -> bool:
    return stack_limit(item_id) > 1


def effective_transfer_qty(item_id: int, qty: int) -> int:
    """Units available to move from a slot.

    PS1 knife (and empty-magazine weapons) keep ``qty == 0`` while occupying a
    slot; treat those as one transferable unit.
    """
    iid = int(item_id) & 0xFF
    if iid == 0:
        return 0
    q = int(qty)
    if q > 0:
        return q
    if iid in WEAPON_ITEM_IDS:
        return 1
    return 0


def _first_empty_slot(slots: list[tuple[int, int]]) -> int | None:
    for i, (item_id, _qty) in enumerate(slots):
        if item_id == 0:
            return i
    return None


def _first_merge_slot(
    slots: list[tuple[int, int]],
    item_id: int,
) -> int | None:
    """First slot with same id that can accept at least one more unit."""
    limit = stack_limit(item_id)
    if limit <= 1:
        return None
    for i, (sid, qty) in enumerate(slots):
        if sid == item_id and qty < limit:
            return i
    return None


def max_transferable(
    dst: list[tuple[int, int]],
    item_id: int,
    qty: int,
) -> int:
    """How many units from a source stack can enter ``dst`` (merge or new slot)."""
    avail = effective_transfer_qty(item_id, qty)
    if avail <= 0:
        return 0
    limit = stack_limit(item_id)
    merge = _first_merge_slot(dst, item_id)
    if merge is not None:
        room = limit - int(dst[merge][1])
        return min(avail, room)
    empty = _first_empty_slot(dst)
    if empty is None:
        return 0
    return min(avail, limit)


def apply_stack_transfer(
    src_slots: list[tuple[int, int]],
    dst_slots: list[tuple[int, int]],
    src_index: int,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], int]:
    """Move as much as possible from ``src_slots[src_index]`` into ``dst_slots``.

    Returns ``(new_src, new_dst, moved_qty)``. Positional gaps in ``src_slots``
    are preserved; overflow stays in the source slot when only a partial merge
    fits.
    """
    new_src = list(src_slots)
    new_dst = list(dst_slots)
    if src_index < 0 or src_index >= len(new_src):
        return new_src, new_dst, 0

    item_id, qty = new_src[src_index]
    avail = effective_transfer_qty(item_id, qty)
    if avail <= 0:
        return new_src, new_dst, 0

    moved = max_transferable(new_dst, item_id, qty)
    if moved <= 0:
        return new_src, new_dst, 0

    limit = stack_limit(item_id)
    merge = _first_merge_slot(new_dst, item_id)
    if merge is not None:
        sid, dq = new_dst[merge]
        new_dst[merge] = (sid, min(int(dq) + moved, limit))
    else:
        empty = _first_empty_slot(new_dst)
        assert empty is not None
        # Preserve knife/empty-weapon RAM qty 0 when moving the whole slot.
        write_qty = moved if int(qty) > 0 else int(qty)
        new_dst[empty] = (item_id, write_qty)

    remaining = avail - moved
    if remaining > 0:
        # Partial moves only happen for qty>0 stacks.
        new_src[src_index] = (item_id, remaining if int(qty) > 0 else 0)
    else:
        new_src[src_index] = (0, 0)

    return new_src, new_dst, moved
