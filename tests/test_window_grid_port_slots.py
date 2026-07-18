"""Port→slot mapping for consistent memlog window placement."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.window_grid import (
    build_slots,
    format_emu_title,
    parse_port_from_title,
    slot_index_for_port,
)


def test_memlog_port_is_top_right_slot() -> None:
    mon = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
    slots = build_slots(20, mon, cols=5, rows=4, gap=8)
    base = 5755
    diag_port = 5759
    slot = slot_index_for_port(diag_port, base_port=base, expected=20)
    assert slot == 4
    # Top-right among first row
    xs_row0 = [slots[i][0] for i in range(5)]
    assert slots[slot][0] == max(xs_row0)
    assert slots[slot][1] == min(s[1] for s in slots[:5])


def test_title_tags() -> None:
    assert format_emu_title(5759, diag=True) == "[p5759] ★ MEMLOG"
    assert format_emu_title(5755, diag=False) == "[p5755]"
    assert parse_port_from_title("[p5759] ★ MEMLOG") == 5759
    assert parse_port_from_title("EmuHawk") is None
