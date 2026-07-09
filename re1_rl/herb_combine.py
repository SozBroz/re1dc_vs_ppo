"""PS1 RE1 Director's Cut herb combine rules (inventory COMBINE menu).

Sources (PS1 original / Director's Cut — not remake):
- Jacko GameFAQs herb FAQ (RE1 DC): all valid permutations
- cheatbook.de reevil.htm item hex + mixture notes
- Sony Director's Cut manual: COMBINE command in inventory

Item IDs (cheatbook.de / GameShark NTSC-U):
  0x43 red   0x44 green   0x45 blue
  0x46 G+R   0x47 G+G    0x48 G+B
  0x49 G+R+B 0x4A G+G+G  0x4B G+G+B

R+B without green is NOT a valid RE1 mixture (absent from Jacko RE1 chart).
Mixed items decompose to counts for pair-wise combine (G+R plus B -> G+R+B).
"""

from __future__ import annotations

from typing import Any, Protocol

from re1_rl.memory_map import INVENTORY_BASE

RED = 0x43
GREEN = 0x44
BLUE = 0x45

MIXED_GR = 0x46
MIXED_GG = 0x47
MIXED_GB = 0x48
MIXED_GRB = 0x49
MIXED_GGG = 0x4A
MIXED_GGB = 0x4B

HERB_ITEM_IDS = frozenset(
    {RED, GREEN, BLUE, MIXED_GR, MIXED_GG, MIXED_GB, MIXED_GRB, MIXED_GGG, MIXED_GGB}
)

_COMBINE_TABLE: dict[tuple[int, int, int], int] = {
    (1, 1, 0): MIXED_GR,
    (0, 2, 0): MIXED_GG,
    (0, 3, 0): MIXED_GGG,
    (0, 1, 1): MIXED_GB,
    (0, 2, 1): MIXED_GGB,
    (1, 1, 1): MIXED_GRB,
}

_DECOMPOSE: dict[int, tuple[int, int, int]] = {
    RED: (1, 0, 0),
    GREEN: (0, 1, 0),
    BLUE: (0, 0, 1),
    MIXED_GR: (1, 1, 0),
    MIXED_GG: (0, 2, 0),
    MIXED_GB: (0, 1, 1),
    MIXED_GRB: (1, 1, 1),
    MIXED_GGG: (0, 3, 0),
    MIXED_GGB: (0, 2, 1),
}


class _BridgeWrite(Protocol):
    def write_ram(self, fields: list[tuple[str, int, str, int]]) -> None: ...


def herb_counts(item_id: int) -> tuple[int, int, int] | None:
    return _DECOMPOSE.get(int(item_id) & 0xFF)


def is_herb_item(item_id: int) -> bool:
    return int(item_id) in HERB_ITEM_IDS


def combine_totals(item_a: int, item_b: int) -> tuple[int, int, int] | None:
    ca = herb_counts(item_a)
    cb = herb_counts(item_b)
    if ca is None or cb is None:
        return None
    r, g, b = ca[0] + cb[0], ca[1] + cb[1], ca[2] + cb[2]
    total = r + g + b
    if total < 2 or total > 3:
        return None
    if r > 1 or b > 1:
        return None
    if r > 0 and g == 0:
        return None
    return (r, g, b)


def combine_product(item_a: int, item_b: int) -> int | None:
    totals = combine_totals(item_a, item_b)
    if totals is None:
        return None
    return _COMBINE_TABLE.get(totals)


def can_combine_slots(
    inventory: list[tuple[int, int]],
    slot_a: int,
    slot_b: int,
) -> bool:
    if slot_a == slot_b:
        return False
    if slot_a < 0 or slot_b < 0 or slot_a >= len(inventory) or slot_b >= len(inventory):
        return False
    id_a, _qa = inventory[slot_a]
    id_b, _qb = inventory[slot_b]
    if id_a == 0 or id_b == 0:
        return False
    return combine_product(id_a, id_b) is not None


def plan_combine(
    inventory: list[tuple[int, int]],
    slot_a: int,
    slot_b: int,
) -> tuple[list[tuple[int, int]], int, int] | None:
    if not can_combine_slots(inventory, slot_a, slot_b):
        return None
    id_a, _ = inventory[slot_a]
    id_b, _ = inventory[slot_b]
    product = combine_product(id_a, id_b)
    assert product is not None

    new_inv = list(inventory)
    dest = min(slot_a, slot_b)
    other = max(slot_a, slot_b)
    new_inv[dest] = (product, 1)
    new_inv[other] = (0, 0)
    return new_inv, dest, product


def combine_slot_from_action(action: int, *, select_slot_base: int) -> int | None:
    slot = int(action) - int(select_slot_base)
    if 0 <= slot < 8:
        return slot
    return None


def combine_slot_pair(slot_a: int, slot_b: int) -> tuple[int, int]:
    return (min(slot_a, slot_b), max(slot_a, slot_b))


def pair_to_index(slot_a: int, slot_b: int) -> int:
    a, b = combine_slot_pair(slot_a, slot_b)
    return a * 7 - (a * (a - 1)) // 2 + (b - a - 1)


def index_to_pair(index: int) -> tuple[int, int]:
    for i in range(8):
        for j in range(i + 1, 8):
            if pair_to_index(i, j) == index:
                return i, j
    raise ValueError(f"bad combine index {index}")


N_COMBINE_PAIRS = 28
