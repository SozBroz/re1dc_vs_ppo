"""Parser for GOG PC savedatN.dat inventory blocks."""

from __future__ import annotations

from pathlib import Path

from re1_rl.memory_map import ITEM_IDS, PC_SAVE_INVENTORY_OFFSET, PC_SAVE_INVENTORY_SLOTS


def _item_name(item_id: int) -> str:
    return ITEM_IDS.get(item_id, f"unknown_0x{item_id:02X}")


def parse_inventory_from_bytes(data: bytes, offset: int = PC_SAVE_INVENTORY_OFFSET) -> list[tuple[str, int]]:
    """Read fixed inventory slots as (name, qty) pairs.

    GOG savedat layout (verified against savedat1–8.dat, Jul 2026):
      - Offset 0x320, 11 slots × 2 bytes (item_id, qty).
      - Empty slot = 00 00; non-contiguous empties occur (e.g. slot 7 empty, slot 8 filled).
      - Trailing sentinel often 09 FF after last item slot.
      - PC item IDs align with PS1 for common guns (0x02 beretta); PS1 spare
        handgun ammo is 0x0B (handgun_bullets), First Aid Spray is 0x41.
    """
    slots: list[tuple[str, int]] = []
    pos = offset
    for _ in range(PC_SAVE_INVENTORY_SLOTS):
        if pos + 1 >= len(data):
            break
        item_id = data[pos]
        qty = data[pos + 1]
        pos += 2
        if item_id == 0 or qty == 0:
            continue
        if item_id == 0x09 and qty == 0xFF:
            break
        slots.append((_item_name(item_id), int(qty)))
    return slots


def parse_save_file(path: str | Path) -> list[tuple[str, int]]:
    """Parse inventory from a savedatN.dat file."""
    p = Path(path)
    data = p.read_bytes()
    return parse_inventory_from_bytes(data)
