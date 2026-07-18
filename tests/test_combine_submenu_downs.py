"""ITEM submenu RAM hooks: COMBN = last entry."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.inventory_menu_macro import combine_submenu_target_index
from re1_rl.memory_map import ITEM_SUBMENU_CURSOR, ITEM_SUBMENU_N_ENTRIES


def test_submenu_hook_addresses() -> None:
    assert ITEM_SUBMENU_CURSOR == 0x800B7FF4
    assert ITEM_SUBMENU_N_ENTRIES == 0x800B7FE9


def test_combine_target_is_last_entry() -> None:
    assert combine_submenu_target_index(3) == 2
    assert combine_submenu_target_index(2) == 1
    assert combine_submenu_target_index(1) == 0
    assert combine_submenu_target_index(0) == 0
