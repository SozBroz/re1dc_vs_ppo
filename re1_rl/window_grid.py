"""Tile BizHawk/EmuHawk windows across monitors (Windows only).

Placement is by **TCP port** (``port = base_port + rank``), not HWND discovery
order. Actors claim ``data/emu_port_by_pid/<pid>`` and stamp titles ``[p5759]``.
The memlog/diag env also gets ``★ MEMLOG`` in the title so it is findable.
"""

from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

import mss

user32 = ctypes.windll.user32
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040
SW_RESTORE = 9
SW_HIDE = 0

TITLE_NEEDLES = ("bizhawk", "emuhawk", "resident evil", "[p")
_SKIP_HWND_TITLE_NEEDLES = (
    "lua console",
    "hex editor",
    "ram search",
    "trace logger",
    "cheat",
    "nut holder",
    "settings",
    "control",
    "about",
)
_SKIP_WINDOW_CLASSES = (
    "#32768",  # popup menu
    "#32769",  # menu
    "sysshadow",
    "tooltips_class32",
    "dropdown",
)
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
# BizHawk main form is WindowsForms10.Window.8.*; owned menu/tool popups are
# Window.20808 / similar and must never be tiled or title-stamped.
_MAIN_FORM_CLASS_RE = re.compile(r"windowsforms10\.window\.8\.", re.IGNORECASE)
_OWNED_POPUP_CLASS_RE = re.compile(r"windowsforms10\.window\.20\d+\.", re.IGNORECASE)
_PORT_TITLE_RE = re.compile(r"\[p(\d+)\]")
_DEFAULT_PORT_MAP = Path("data") / "emu_port_by_pid"


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


MONITOR_DEFAULTTONEAREST = 2


def port_map_dir(project_root: Path | None = None) -> Path:
    raw = os.environ.get("RE1_EMU_PORT_MAP", "").strip()
    if raw:
        return Path(raw)
    if project_root is not None:
        return Path(project_root) / _DEFAULT_PORT_MAP
    return Path(_DEFAULT_PORT_MAP)


def claim_emu_port(pid: int, port: int, *, project_root: Path | None = None) -> Path:
    """Record which TCP port an EmuHawk process owns (tiler reads this)."""
    d = port_map_dir(project_root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / str(int(pid))
    path.write_text(str(int(port)), encoding="ascii")
    return path


def release_emu_port(pid: int, *, project_root: Path | None = None) -> None:
    path = port_map_dir(project_root) / str(int(pid))
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def lookup_port_for_pid(pid: int, *, project_root: Path | None = None) -> int | None:
    path = port_map_dir(project_root) / str(int(pid))
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def parse_port_from_title(title: str) -> int | None:
    m = _PORT_TITLE_RE.search(title or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def format_emu_title(port: int, *, diag: bool = False) -> str:
    """Stable title tag so humans and the tiler can find the window."""
    if diag:
        return f"[p{int(port)}] ★ MEMLOG"
    return f"[p{int(port)}]"


def slot_index_for_port(port: int, *, base_port: int, expected: int) -> int | None:
    slot = int(port) - int(base_port)
    if 0 <= slot < int(expected):
        return slot
    return None


def list_monitors() -> list[dict[str, int]]:
    with mss.mss() as sct:
        return [
            {
                "left": int(m["left"]),
                "top": int(m["top"]),
                "width": int(m["width"]),
                "height": int(m["height"]),
            }
            for m in sct.monitors[1:]
        ]


def pick_monitors(monitors: list[dict[str, int]], which: str | None) -> list[dict[str, int]]:
    if not monitors:
        raise RuntimeError("no monitors detected")
    if not which or which == "all":
        return monitors
    w = which.casefold()
    if w == "right":
        return [max(monitors, key=lambda m: m["left"])]
    if w == "left":
        return [min(monitors, key=lambda m: m["left"])]
    if w == "center":
        ordered = sorted(monitors, key=lambda m: m["left"])
        return [ordered[len(ordered) // 2]]
    if w.isdigit():
        idx = int(w) - 1
        if idx < 0 or idx >= len(monitors):
            raise ValueError(f"monitor index {w} out of range (1..{len(monitors)})")
        return [monitors[idx]]
    raise ValueError(f"unknown monitor {which!r}; use left|center|right|N|all")


def taskbar_reserve_extra_px() -> int:
    """Legacy alias for optional extra bottom inset (``RE1_GRID_TASKBAR_RESERVE``)."""
    return grid_bottom_inset_extra_px()


def grid_bottom_inset_px() -> int:
    """Pixels to leave clear at the monitor bottom (taskbar). ``RE1_GRID_BOTTOM_INSET``."""
    raw = os.environ.get("RE1_GRID_BOTTOM_INSET", "48").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 48


def grid_bottom_inset_extra_px() -> int:
    raw = os.environ.get("RE1_GRID_TASKBAR_RESERVE", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def grid_lock_interval_s() -> float:
    """How often the tiler re-checks HWND geometry when lock is on."""
    raw = os.environ.get("RE1_GRID_LOCK_INTERVAL_S", "1.5").strip()
    try:
        return max(0.25, float(raw))
    except ValueError:
        return 1.5


def grid_position_tolerance_px() -> int:
    raw = os.environ.get("RE1_GRID_POSITION_TOL_PX", "6").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 6


def grid_force_lock() -> bool:
    raw = os.environ.get("RE1_GRID_FORCE_LOCK", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def grid_chromeless_shells() -> bool:
    """Headless (--gdi --chromeless) spawns a tiny host plus a fullscreen shell."""
    raw = os.environ.get("RE1_GRID_CHROMELESS_SHELLS", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _tile_hwnd_score(
    hwnd: int,
    *,
    port: int | None = None,
    target: tuple[int, int, int, int] | None = None,
) -> int:
    """Lower score = better tile candidate (avoid fullscreen shells)."""
    _, _, w, h = _window_outer_rect(hwnd)
    area = max(0, int(w)) * max(0, int(h))
    score = area
    title = _window_title(hwnd)
    low = title.casefold()
    if port is not None and f"[p{int(port)}]" in title:
        score -= 5_000_000
    elif title.startswith("[p"):
        score -= 2_000_000
    if any(n in low for n in ("resident evil", "emuhawk", "bizhawk")):
        score -= 500_000
    if target is not None:
        _, _, tw, th = target
        if grid_chromeless_shells():
            if int(w) > int(tw) + 40 or int(h) > int(th) + 40:
                score += 100_000_000
        else:
            # Visible mode: tile the main game window, not tiny chromeless hosts.
            if int(w) < 300 or int(h) < 220:
                score += 100_000_000
            if "resident evil" in low:
                score -= 2_000_000
        size_delta = abs(int(w) - int(tw)) + abs(int(h) - int(th))
        score += size_delta * 100
    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        score += 50_000_000
    return int(score)


def _outer_rect_needs_tile(
    cur: tuple[int, int, int, int],
    target: tuple[int, int, int, int],
    *,
    tol: int | None = None,
) -> bool:
    """True when outer window rect is not already at the grid slot."""
    if tol is None:
        tol = grid_position_tolerance_px()
    cx, cy, cw, ch = (int(v) for v in cur)
    tx, ty, tw, th = (int(v) for v in target)
    if abs(cw - tw) > tol or abs(ch - th) > tol:
        return True
    if abs(cx - tx) > tol or abs(cy - ty) > tol:
        return True
    return False


def _apply_bottom_reserve(mon: dict[str, int], reserve: int) -> dict[str, int]:
    if reserve <= 0:
        return mon
    return {
        **mon,
        "height": max(200, int(mon["height"]) - int(reserve)),
    }


def monitor_work_area(mon: dict[str, int]) -> dict[str, int]:
    """Shrink monitor bounds to the Windows work rect (excludes taskbar/dock)."""
    pt = _POINT(
        int(mon["left"] + mon["width"] // 2),
        int(mon["top"] + mon["height"] // 2),
    )
    hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return _apply_bottom_reserve(mon, taskbar_reserve_extra_px())
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return _apply_bottom_reserve(mon, taskbar_reserve_extra_px())
    work = info.rcWork
    out = {
        "left": int(work.left),
        "top": int(work.top),
        "width": int(work.right - work.left),
        "height": int(work.bottom - work.top),
    }
    return _apply_bottom_reserve(out, taskbar_reserve_extra_px())


def prepare_grid_monitors(
    monitors: list[dict[str, int]], which: str | None
) -> list[dict[str, int]]:
    """Pick target monitor(s). Full monitor bounds; taskbar handled in slot layout."""
    picked = pick_monitors(monitors, which)
    use_work = os.environ.get("RE1_GRID_USE_WORK_AREA", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if use_work:
        return [monitor_work_area(mon) for mon in picked]
    return picked


def build_slots(
    count: int,
    monitors: list[dict[str, int]],
    *,
    cols: int,
    rows: int,
    gap: int,
) -> list[tuple[int, int, int, int]]:
    per_monitor = cols * rows
    if not monitors:
        raise RuntimeError("no monitors detected")
    if count > per_monitor * len(monitors):
        raise ValueError(
            f"need {count} slots but only {per_monitor * len(monitors)} "
            f"({cols}x{rows} on {len(monitors)} monitor(s))"
        )
    slots: list[tuple[int, int, int, int]] = []
    for i in range(count):
        mon = monitors[(i // per_monitor) % len(monitors)]
        local = i % per_monitor
        col = local % cols
        row = local // cols
        slots.append(_slot_rect(mon, col, row, cols=cols, rows=rows, gap=gap))
    return slots


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    return buf.value


def _set_window_title(hwnd: int, title: str) -> None:
    user32.SetWindowTextW(hwnd, str(title))


def _pid_for_hwnd(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _enum_bizhawk_windows() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if _is_skipped_emu_hwnd(hwnd):
            return True
        title = _window_title(hwnd)
        if not title:
            return True
        low = title.casefold()
        if any(n in low for n in TITLE_NEEDLES) or title.startswith("[p"):
            out.append((hwnd, title))
        return True

    user32.EnumWindows(cb, 0)
    out.sort(key=lambda x: x[0])
    return out


def find_hwnds_for_pid(
    pid: int,
    *,
    require_visible: bool = True,
    require_title: bool = True,
) -> list[int]:
    """Top-level windows owned by ``pid`` (optional visibility/title filters)."""
    want = int(pid)
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd: int, _: int) -> bool:
        if require_visible and not user32.IsWindowVisible(hwnd):
            return True
        if _pid_for_hwnd(hwnd) != want:
            return True
        if require_title and not _window_title(hwnd):
            return True
        found.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return found


def iter_claimed_emu_windows(
    project_root: Path | None,
    *,
    base_port: int = 5755,
    expected: int | None = None,
    slots: list[tuple[int, int, int, int]] | None = None,
) -> list[tuple[int, int, str]]:
    """Return ``(hwnd, port, title)`` from ``data/emu_port_by_pid`` claims.

    Chromeless/--gdi EmuHawk often has an empty title until the tiler stamps
    ``[pPORT]``; title-based discovery alone never places those windows.
    """
    d = port_map_dir(project_root)
    if not d.is_dir():
        return []
    out: list[tuple[int, int, str]] = []
    for path in sorted(d.iterdir()):
        if not path.is_file():
            continue
        try:
            pid = int(path.name)
            port = int(path.read_text(encoding="ascii").strip())
        except (ValueError, OSError):
            continue
        target = None
        if slots is not None and expected is not None:
            slot_idx = slot_index_for_port(port, base_port=base_port, expected=expected)
            if slot_idx is not None:
                target = slots[slot_idx]
        hwnd = _best_hwnd_for_pid(pid, port=port, target=target)
        if hwnd is None:
            continue
        out.append((hwnd, port, _window_title(hwnd)))
    return out


def set_emu_window_title(
    pid: int,
    title: str,
    *,
    retries: int = 20,
    delay_s: float = 0.25,
) -> int | None:
    """Set EmuHawk main window title for ``pid``. Return hwnd when found."""
    for _ in range(max(1, retries)):
        hwnd = _best_hwnd_for_pid(pid)
        if hwnd is not None:
            _set_window_title(hwnd, title)
            return hwnd
        time.sleep(delay_s)
    return None


def stamp_emu_window(
    pid: int,
    port: int,
    *,
    diag: bool = False,
    retries: int = 20,
    delay_s: float = 0.25,
) -> int | None:
    """Stamp EmuHawk title with ``[pPORT]`` (and ★ MEMLOG when diag). Return hwnd."""
    return set_emu_window_title(
        pid,
        format_emu_title(port, diag=diag),
        retries=retries,
        delay_s=delay_s,
    )


def _resolve_port(
    hwnd: int,
    title: str,
    *,
    project_root: Path | None,
) -> int | None:
    port = parse_port_from_title(title)
    if port is not None:
        return port
    return lookup_port_for_pid(_pid_for_hwnd(hwnd), project_root=project_root)


def _window_outer_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _hwnd_area(hwnd: int) -> int:
    _, _, w, h = _window_outer_rect(hwnd)
    return max(0, int(w)) * max(0, int(h))


def _is_bizhawk_main_hwnd(hwnd: int) -> bool:
    """True for the real EmuHawk game form (not menu/tool owned popups)."""
    cls = _window_class(hwnd)
    if _OWNED_POPUP_CLASS_RE.search(cls):
        return False
    style = int(user32.GetWindowLongW(hwnd, GWL_STYLE))
    if (style & WS_POPUP) and not (style & WS_CAPTION):
        return False
    if _MAIN_FORM_CLASS_RE.search(cls):
        return True
    # Chromeless/--gdi hosts may not match Window.8; require a real captioned frame.
    return bool(style & WS_CAPTION) and bool(style & WS_THICKFRAME)


def _is_skipped_emu_hwnd(hwnd: int) -> bool:
    """Drop tool/auxiliary BizHawk windows (Lua Console, menus, popups, etc.)."""
    cls = _window_class(hwnd).casefold()
    if any(skip in cls for skip in _SKIP_WINDOW_CLASSES):
        return True
    if _OWNED_POPUP_CLASS_RE.search(cls):
        return True
    style = int(user32.GetWindowLongW(hwnd, GWL_STYLE))
    # WinForms menu dropdowns are captionless WS_POPUP; tiling them causes the
    # "menus flickering across the screen" and steals [pPORT] title stamps.
    if (style & WS_POPUP) and not (style & WS_CAPTION):
        return True
    ex_style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    _, _, w, h = _window_outer_rect(hwnd)
    area = max(0, w) * max(0, h)
    if (ex_style & WS_EX_TOOLWINDOW) and area < 200_000:
        return True
    if h > 0 and w > h * 4 and h < 160:
        return True
    low = _window_title(hwnd).casefold()
    if not low:
        return False
    if low == "lua":
        return True
    return any(needle in low for needle in _SKIP_HWND_TITLE_NEEDLES)


def _best_hwnd_for_pid(
    pid: int,
    *,
    port: int | None = None,
    target: tuple[int, int, int, int] | None = None,
) -> int | None:
    """Pick the game host window; never menus / owned popups."""
    hwnds = find_hwnds_for_pid(pid, require_visible=False, require_title=False)
    if not hwnds:
        return None
    best: tuple[int, int] | None = None
    for hwnd in hwnds:
        if _is_skipped_emu_hwnd(hwnd) or not _is_bizhawk_main_hwnd(hwnd):
            continue
        if not user32.IsWindowVisible(hwnd) and not grid_chromeless_shells():
            # Visible-mode: only tile the live main form, not a hidden shell.
            continue
        _, _, w, h = _window_outer_rect(hwnd)
        if h < 180:
            continue
        if _hwnd_area(hwnd) < 25_000:
            continue
        score = _tile_hwnd_score(hwnd, port=port, target=target)
        if best is None or score < best[0]:
            best = (score, int(hwnd))
    if best is not None:
        return best[1]
    for hwnd in hwnds:
        if (
            not _is_skipped_emu_hwnd(hwnd)
            and _is_bizhawk_main_hwnd(hwnd)
            and user32.IsWindowVisible(hwnd)
        ):
            return int(hwnd)
    # C-RE1 / SDL hosts are not WinForms BizHawk — take largest visible frame.
    fallback: tuple[int, int] | None = None
    for hwnd in hwnds:
        if _is_skipped_emu_hwnd(hwnd) or not user32.IsWindowVisible(hwnd):
            continue
        _, _, w, h = _window_outer_rect(hwnd)
        if h < 180 or _hwnd_area(hwnd) < 25_000:
            continue
        area = _hwnd_area(hwnd)
        if fallback is None or area > fallback[0]:
            fallback = (area, int(hwnd))
    return None if fallback is None else fallback[1]


def _normalize_window_for_tile(hwnd: int) -> None:
    """One-shot un-maximize before placement; avoid WM_SYSCOMMAND (menu flicker)."""
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)


def _window_needs_force_tile(
    hwnd: int,
    target: tuple[int, int, int, int],
) -> bool:
    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        return True
    return _outer_rect_needs_tile(_window_outer_rect(hwnd), target)


def _suppress_extra_emu_windows(
    pid: int,
    keep_hwnd: int,
    *,
    target: tuple[int, int, int, int] | None = None,
) -> int:
    """Hide maximized/default BizHawk shells (chromeless headless only)."""
    if not grid_chromeless_shells():
        return 0
    _, _, tw, th = target or (0, 0, 374, 248)
    max_w = int(tw) + 80
    max_h = int(th) + 80
    hidden = 0
    for hwnd in find_hwnds_for_pid(pid, require_visible=False, require_title=False):
        if int(hwnd) == int(keep_hwnd) or _is_skipped_emu_hwnd(hwnd):
            continue
        if not user32.IsWindowVisible(hwnd):
            continue
        _, _, w, h = _window_outer_rect(hwnd)
        title = _window_title(hwnd)
        low = title.casefold()
        hide = False
        if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
            hide = True
        elif int(w) > max_w or int(h) > max_h:
            hide = True
        elif title and not title.startswith("[p") and (
            "bizhawk" in low or "resident evil" in low or "emuhawk" in low
        ):
            hide = True
        if hide:
            user32.ShowWindow(hwnd, SW_HIDE)
            hidden += 1
    return hidden


def _place_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    target = (int(x), int(y), int(w), int(h))
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        _normalize_window_for_tile(hwnd)
    user32.SetWindowPos(
        hwnd,
        0,
        target[0],
        target[1],
        target[2],
        target[3],
        SWP_NOZORDER | SWP_SHOWWINDOW,
    )


def _slot_rect(
    monitor: dict[str, int],
    col: int,
    row: int,
    *,
    cols: int,
    rows: int,
    gap: int,
) -> tuple[int, int, int, int]:
    bottom_inset = grid_bottom_inset_px() + grid_bottom_inset_extra_px()
    usable_w = monitor["width"] - gap * (cols + 1)
    usable_h = monitor["height"] - gap * (rows + 1) - bottom_inset
    cell_w = max(280, usable_w // cols)
    cell_h = max(200, usable_h // rows)
    if cell_h * rows > usable_h:
        cell_h = max(180, usable_h // rows)
    if cell_w * cols > usable_w:
        cell_w = max(240, usable_w // cols)
    x = monitor["left"] + gap + col * (cell_w + gap)
    y = monitor["top"] + gap + row * (cell_h + gap)
    return x, y, cell_w, cell_h


def tile_loop(
    *,
    expected: int,
    monitors: list[dict[str, int]],
    cols: int,
    rows: int,
    gap: int,
    stop: threading.Event,
    interval: float | None = None,
    lock_windows: bool = True,
    log_fn: Callable[[str], None] | None = None,
    base_port: int = 5755,
    project_root: Path | None = None,
    diag_port: int | None = None,
) -> None:
    def _log(msg: str) -> None:
        if log_fn is not None:
            log_fn(msg)
        else:
            print(msg, flush=True)

    slots = build_slots(expected, monitors, cols=cols, rows=rows, gap=gap)
    poll_s = float(interval if interval is not None else grid_lock_interval_s())
    # hwnd -> (slot_idx, port); port -> hwnd for one-window-per-port
    placed: dict[int, tuple[int, int]] = {}
    port_hwnd: dict[int, int] = {}
    hidden_shells: set[int] = set()
    per_monitor = cols * rows
    lock_note = ", lock=on" if lock_windows else ""
    force_note = ", force=on" if grid_force_lock() else ""
    _log(
        f"[fleet-grid] tiling up to {expected} windows by port "
        f"(base_port={base_port}) - {len(monitors)} monitor(s), "
        f"{cols}x{rows} grid, gap={gap}px, poll={poll_s:.2f}s{lock_note}{force_note}"
    )
    if monitors:
        mon = monitors[0]
        _log(
            f"[fleet-grid] grid monitor 1: "
            f"{mon['width']}x{mon['height']} at ({mon['left']},{mon['top']}), "
            f"bottom_inset={grid_bottom_inset_px() + grid_bottom_inset_extra_px()}px"
        )
    if diag_port is not None:
        # ASCII-only log text: Windows console often uses cp1252 and will kill
        # the tiler thread on UnicodeEncodeError for arrows/stars.
        _log(
            f"[fleet-grid] memlog/diag port {diag_port} -> slot "
            f"{diag_port - base_port} title tag '* MEMLOG'"
        )
    def _place_port_window(hwnd: int, port: int, title: str) -> None:
        if _is_skipped_emu_hwnd(hwnd) or not _is_bizhawk_main_hwnd(hwnd):
            return
        if _hwnd_area(hwnd) < 25_000:
            return
        slot_idx = slot_index_for_port(port, base_port=base_port, expected=expected)
        if slot_idx is None:
            return
        x, y, w, h = slots[slot_idx]
        target = (x, y, w, h)
        existing = port_hwnd.get(port)
        if existing is not None and existing != hwnd:
            if (
                user32.IsWindow(existing)
                and not _is_skipped_emu_hwnd(existing)
                and _is_bizhawk_main_hwnd(existing)
            ):
                if _tile_hwnd_score(existing, port=port, target=target) <= _tile_hwnd_score(
                    hwnd, port=port, target=target
                ):
                    return
                placed.pop(existing, None)
            port_hwnd.pop(port, None)
        want_title = format_emu_title(
            port, diag=(diag_port is not None and port == diag_port)
        )
        prev = placed.get(hwnd)
        if prev is None or prev[0] != slot_idx:
            _place_window(hwnd, x, y, w, h)
            pid = _pid_for_hwnd(hwnd)
            hid = _suppress_extra_emu_windows(pid, hwnd, target=target)
            if hid and hwnd not in hidden_shells:
                _log(f"[fleet-grid] hid {hid} extra shell window(s) for port {port}")
                hidden_shells.add(hwnd)
            # Stamp title only after move, from the tiler (not the actor).
            if title != want_title:
                _set_window_title(hwnd, want_title)
            placed[hwnd] = (slot_idx, port)
            port_hwnd[port] = hwnd
            if prev is None:
                mon_idx = (slot_idx // per_monitor) % len(monitors)
                local = slot_idx % per_monitor
                # Keep log ASCII (cp1252 consoles); window titles may still use ★.
                safe_title = want_title.replace("★", "*")
                _log(
                    f"[fleet-grid] port {port} -> slot {slot_idx} "
                    f"monitor {mon_idx + 1} ({local % cols},{local // cols}) "
                    f"- {safe_title!r}"
                )
        else:
            placed[hwnd] = (slot_idx, port)
            port_hwnd[port] = hwnd
            if _window_needs_force_tile(hwnd, target):
                _place_window(hwnd, x, y, w, h)
            # Keep renaming the main form even if BizHawk restores the ROM title.
            cur_title = _window_title(hwnd)
            if cur_title != want_title:
                _set_window_title(hwnd, want_title)

    initial_done = False
    while not stop.is_set():
        seen: set[int] = set()
        for hwnd, title in _enum_bizhawk_windows():
            if not title.startswith("[p"):
                continue
            seen.add(hwnd)
            port = _resolve_port(hwnd, title, project_root=project_root)
            if port is None:
                continue
            _place_port_window(hwnd, port, title)
        for hwnd, port, title in iter_claimed_emu_windows(
            project_root,
            base_port=base_port,
            expected=expected,
            slots=slots,
        ):
            if hwnd in seen:
                existing = port_hwnd.get(port)
                if existing == hwnd:
                    continue
            _place_port_window(hwnd, port, title)

        if len(placed) >= expected and not initial_done:
            _log("[fleet-grid] all port-mapped windows placed")
            initial_done = True
            if not lock_windows:
                break

        if lock_windows and placed:
            dead: list[int] = []
            for hwnd, (slot_idx, port) in list(placed.items()):
                if (
                    not user32.IsWindow(hwnd)
                    or _is_skipped_emu_hwnd(hwnd)
                    or not _is_bizhawk_main_hwnd(hwnd)
                ):
                    dead.append(hwnd)
                    port_hwnd.pop(port, None)
                    continue
                x, y, w, h = slots[slot_idx]
                target = (x, y, w, h)
                if grid_force_lock() or _window_needs_force_tile(hwnd, target):
                    _place_window(hwnd, x, y, w, h)
                # Re-stamp so BizHawk ROM title churn does not erase [pPORT] / MEMLOG.
                want_title = format_emu_title(
                    port, diag=(diag_port is not None and port == diag_port)
                )
                cur_title = _window_title(hwnd)
                if cur_title != want_title:
                    _set_window_title(hwnd, want_title)
            for hwnd in dead:
                del placed[hwnd]

        if not lock_windows and initial_done:
            break
        time.sleep(poll_s)


def start_grid_tiler(
    *,
    expected: int,
    cols: int = 4,
    rows: int = 2,
    gap: int = 8,
    monitor: str = "all",
    lock_windows: bool = True,
    interval: float | None = None,
    log_fn: Callable[[str], None] | None = None,
    base_port: int = 5755,
    project_root: Path | str | None = None,
    diag_port: int | None = None,
) -> tuple[threading.Event, threading.Thread]:
    """Start a daemon thread that tiles BizHawk windows. Returns (stop, thread)."""
    all_monitors = list_monitors()
    target_monitors = prepare_grid_monitors(all_monitors, monitor)
    root = Path(project_root) if project_root is not None else None
    if diag_port is None:
        raw = os.environ.get("RE1_STEP_DIAG_PORT", "").strip()
        if raw.isdigit():
            diag_port = int(raw)
    stop = threading.Event()
    poll_s = float(interval if interval is not None else grid_lock_interval_s())
    thread = threading.Thread(
        target=tile_loop,
        kwargs={
            "expected": expected,
            "monitors": target_monitors,
            "cols": cols,
            "rows": rows,
            "gap": gap,
            "stop": stop,
            "interval": poll_s,
            "lock_windows": lock_windows,
            "log_fn": log_fn,
            "base_port": int(base_port),
            "project_root": root,
            "diag_port": diag_port,
        },
        name="bizhawk-grid-tiler",
        daemon=True,
    )
    thread.start()
    return stop, thread
