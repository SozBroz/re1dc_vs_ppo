"""Tile BizHawk/EmuHawk windows across monitors (Windows only)."""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

import mss

user32 = ctypes.windll.user32
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040

TITLE_NEEDLES = ("bizhawk", "emuhawk", "resident evil")


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


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


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    return buf.value


def _enum_bizhawk_windows() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title:
            return True
        low = title.casefold()
        if any(n in low for n in TITLE_NEEDLES):
            out.append((hwnd, title))
        return True

    user32.EnumWindows(cb, 0)
    out.sort(key=lambda x: x[0])
    return out


def _window_outer_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _place_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    user32.SetWindowPos(
        hwnd,
        0,
        int(x),
        int(y),
        int(w),
        int(h),
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
    usable_w = monitor["width"] - gap * (cols + 1)
    usable_h = monitor["height"] - gap * (rows + 1)
    cell_w = max(320, usable_w // cols)
    cell_h = max(240, usable_h // rows)
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
    interval: float = 1.5,
    lock_windows: bool = True,
    log_fn: Callable[[str], None] | None = None,
) -> None:
    def _log(msg: str) -> None:
        if log_fn is not None:
            log_fn(msg)
        else:
            print(msg, flush=True)

    slots = build_slots(expected, monitors, cols=cols, rows=rows, gap=gap)
    placed: dict[int, int] = {}
    per_monitor = cols * rows
    lock_note = ", lock=on" if lock_windows else ""
    _log(
        f"[fleet-grid] tiling up to {expected} windows — "
        f"{len(monitors)} target monitor(s), {cols}x{rows} grid, gap={gap}px{lock_note}"
    )
    initial_done = False
    while not stop.is_set():
        windows = _enum_bizhawk_windows()
        for hwnd, title in windows:
            if hwnd in placed:
                continue
            slot_idx = len(placed)
            if slot_idx >= expected:
                break
            x, y, w, h = slots[slot_idx]
            _place_window(hwnd, x, y, w, h)
            placed[hwnd] = slot_idx
            mon_idx = (slot_idx // per_monitor) % len(monitors)
            local = slot_idx % per_monitor
            _log(
                f"[fleet-grid] window {slot_idx + 1}/{expected} "
                f"monitor {mon_idx + 1} slot ({local % cols},{local // cols}) — {title!r}"
            )
        if len(placed) >= expected and not initial_done:
            _log("[fleet-grid] all windows placed")
            initial_done = True
            if not lock_windows:
                break

        if lock_windows and placed:
            dead: list[int] = []
            for hwnd, slot_idx in placed.items():
                if not user32.IsWindow(hwnd):
                    dead.append(hwnd)
                    continue
                x, y, w, h = slots[slot_idx]
                cur = _window_outer_rect(hwnd)
                target = (x, y, w, h)
                if cur != target:
                    _place_window(hwnd, x, y, w, h)
            for hwnd in dead:
                del placed[hwnd]

        if not lock_windows and initial_done:
            break
        time.sleep(interval)


def start_grid_tiler(
    *,
    expected: int,
    cols: int = 4,
    rows: int = 2,
    gap: int = 8,
    monitor: str = "all",
    lock_windows: bool = True,
    interval: float = 1.5,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[threading.Event, threading.Thread]:
    """Start a daemon thread that tiles BizHawk windows. Returns (stop, thread)."""
    all_monitors = list_monitors()
    target_monitors = pick_monitors(all_monitors, monitor)
    stop = threading.Event()
    thread = threading.Thread(
        target=tile_loop,
        kwargs={
            "expected": expected,
            "monitors": target_monitors,
            "cols": cols,
            "rows": rows,
            "gap": gap,
            "stop": stop,
            "interval": interval,
            "lock_windows": lock_windows,
            "log_fn": log_fn,
        },
        name="bizhawk-grid-tiler",
        daemon=True,
    )
    thread.start()
    return stop, thread
