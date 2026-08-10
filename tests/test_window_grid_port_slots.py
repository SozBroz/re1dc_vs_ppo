"""Port→slot mapping for consistent memlog window placement."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.window_grid import (
    _apply_bottom_reserve,
    _outer_rect_needs_tile,
    _tile_hwnd_score,
    build_slots,
    format_emu_title,
    iter_claimed_emu_windows,
    parse_port_from_title,
    port_map_dir,
    slot_index_for_port,
)


def test_tile_hwnd_score_prefers_slot_sized_window(monkeypatch) -> None:
    from re1_rl import window_grid as wg

    monkeypatch.setenv("RE1_GRID_CHROMELESS_SHELLS", "1")
    target = (100, 50, 374, 248)
    monkeypatch.setattr(wg, "_window_outer_rect", lambda hwnd: (0, 0, 1920, 1080) if hwnd == 1 else (0, 0, 374, 248))
    monkeypatch.setattr(wg, "_window_title", lambda hwnd: "[p5759]" if hwnd == 2 else "EmuHawk")
    monkeypatch.setattr(wg, "user32", type("U", (), {"IsZoomed": staticmethod(lambda _h: False), "IsIconic": staticmethod(lambda _h: False)})())
    assert _tile_hwnd_score(1, port=5759, target=target) > _tile_hwnd_score(2, port=5759, target=target)


def test_outer_rect_needs_tile_tolerance() -> None:
    target = (100, 50, 360, 240)
    assert not _outer_rect_needs_tile((100, 50, 360, 240), target, tol=6)
    assert not _outer_rect_needs_tile((103, 52, 358, 242), target, tol=6)
    assert _outer_rect_needs_tile((120, 50, 360, 240), target, tol=6)
    assert _outer_rect_needs_tile((100, 50, 1920, 1080), target, tol=6)


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


def test_skipped_emu_hwnd_rejects_lua_console(monkeypatch) -> None:
    from re1_rl import window_grid as wg

    monkeypatch.setattr(wg, "_window_class", lambda _hwnd: "WindowsForms10.Window.8.app")
    monkeypatch.setattr(wg, "_window_outer_rect", lambda _hwnd: (0, 0, 400, 300))
    monkeypatch.setattr(
        wg.user32,
        "GetWindowLongW",
        lambda _hwnd, idx: 0x16CF0000 if idx == wg.GWL_STYLE else 0,
    )
    monkeypatch.setattr(wg, "_window_title", lambda _hwnd: "Lua Console")
    assert wg._is_skipped_emu_hwnd(1)
    monkeypatch.setattr(wg, "_window_title", lambda _hwnd: "[p5759]")
    assert not wg._is_skipped_emu_hwnd(1)


def test_skipped_emu_hwnd_rejects_menu_popups(monkeypatch) -> None:
    from re1_rl import window_grid as wg

    monkeypatch.setattr(
        wg, "_window_class", lambda _hwnd: "WindowsForms10.Window.20808.app.0.x"
    )
    monkeypatch.setattr(wg, "_window_outer_rect", lambda _hwnd: (256, 208, 319, 236))
    monkeypatch.setattr(wg, "_window_title", lambda _hwnd: "[p5757]")
    # Captionless WS_POPUP (BizHawk menu dropdown).
    monkeypatch.setattr(
        wg.user32,
        "GetWindowLongW",
        lambda _hwnd, idx: 0x96000000 if idx == wg.GWL_STYLE else 0,
    )
    assert wg._is_skipped_emu_hwnd(1)
    assert not wg._is_bizhawk_main_hwnd(1)


def test_is_bizhawk_main_hwnd_accepts_captioned_form(monkeypatch) -> None:
    from re1_rl import window_grid as wg

    monkeypatch.setattr(
        wg, "_window_class", lambda _hwnd: "WindowsForms10.Window.8.app.0.x"
    )
    monkeypatch.setattr(
        wg.user32,
        "GetWindowLongW",
        lambda _hwnd, idx: 0x16CF0000 if idx == wg.GWL_STYLE else 0,
    )
    assert wg._is_bizhawk_main_hwnd(1)


def test_apply_bottom_reserve_shrinks_height() -> None:
    mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    out = _apply_bottom_reserve(mon, 48)
    assert out["height"] == 1032


def test_slot_layout_reserves_bottom_inset(monkeypatch) -> None:
    monkeypatch.setenv("RE1_GRID_BOTTOM_INSET", "48")
    from re1_rl import window_grid as wg

    monkeypatch.setattr(wg, "grid_bottom_inset_px", lambda: 48)
    monkeypatch.setattr(wg, "grid_bottom_inset_extra_px", lambda: 0)
    mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    _, _, _, h = wg._slot_rect(mon, 0, 3, cols=5, rows=4, gap=8)
    _, _, _, h_top = wg._slot_rect(mon, 0, 0, cols=5, rows=4, gap=8)
    assert h == h_top
    slots = build_slots(20, [mon], cols=5, rows=4, gap=8)
    assert slots[19][1] + slots[19][3] <= 1080


def test_iter_claimed_emu_windows_reads_port_map(tmp_path: Path) -> None:
    d = port_map_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "4242").write_text("5757", encoding="ascii")
    # No live EmuHawk on CI — just ensure the scanner tolerates missing HWNDs.
    assert iter_claimed_emu_windows(tmp_path) == []
