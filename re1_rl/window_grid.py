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

TITLE_NEEDLES = ("bizhawk", "emuhawk", "resident evil", "[p")
_PORT_TITLE_RE = re.compile(r"\[p(\d+)\]")
_DEFAULT_PORT_MAP = Path("data") / "emu_port_by_pid"


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


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


def find_hwnds_for_pid(pid: int) -> list[int]:
    """Visible top-level windows owned by ``pid`` (no title filter)."""
    want = int(pid)
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if _pid_for_hwnd(hwnd) != want:
            return True
        if not _window_title(hwnd):
            return True
        found.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return found


def set_emu_window_title(
    pid: int,
    title: str,
    *,
    retries: int = 20,
    delay_s: float = 0.25,
) -> int | None:
    """Set EmuHawk main window title for ``pid``. Return hwnd when found."""
    for _ in range(max(1, retries)):
        hwnds = find_hwnds_for_pid(pid)
        if hwnds:
            hwnd = hwnds[0]
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
    # hwnd -> (slot_idx, port)
    placed: dict[int, tuple[int, int]] = {}
    per_monitor = cols * rows
    lock_note = ", lock=on" if lock_windows else ""
    _log(
        f"[fleet-grid] tiling up to {expected} windows by port "
        f"(base_port={base_port}) - {len(monitors)} monitor(s), "
        f"{cols}x{rows} grid, gap={gap}px{lock_note}"
    )
    if diag_port is not None:
        # ASCII-only log text: Windows console often uses cp1252 and will kill
        # the tiler thread on UnicodeEncodeError for arrows/stars.
        _log(
            f"[fleet-grid] memlog/diag port {diag_port} -> slot "
            f"{diag_port - base_port} title tag '* MEMLOG'"
        )
    initial_done = False
    while not stop.is_set():
        windows = _enum_bizhawk_windows()
        for hwnd, title in windows:
            port = _resolve_port(hwnd, title, project_root=project_root)
            if port is None:
                continue
            slot_idx = slot_index_for_port(
                port, base_port=base_port, expected=expected
            )
            if slot_idx is None:
                continue
            want_title = format_emu_title(
                port, diag=(diag_port is not None and port == diag_port)
            )
            x, y, w, h = slots[slot_idx]
            prev = placed.get(hwnd)
            if prev is None or prev[0] != slot_idx:
                _place_window(hwnd, x, y, w, h)
                # Stamp title only after move, from the tiler (not the actor).
                if title != want_title:
                    _set_window_title(hwnd, want_title)
                placed[hwnd] = (slot_idx, port)
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

        if len(placed) >= expected and not initial_done:
            _log("[fleet-grid] all port-mapped windows placed")
            initial_done = True
            if not lock_windows:
                break

        if lock_windows and placed:
            dead: list[int] = []
            for hwnd, (slot_idx, port) in list(placed.items()):
                if not user32.IsWindow(hwnd):
                    dead.append(hwnd)
                    continue
                x, y, w, h = slots[slot_idx]
                cur = _window_outer_rect(hwnd)
                target = (x, y, w, h)
                if cur != target:
                    _place_window(hwnd, x, y, w, h)
                # Re-stamp occasionally so BizHawk ROM title churn does not erase MEMLOG.
                want_title = format_emu_title(
                    port, diag=(diag_port is not None and port == diag_port)
                )
                cur_title = _window_title(hwnd)
                if cur_title != want_title and "[p" not in (cur_title or ""):
                    _set_window_title(hwnd, want_title)
                elif diag_port is not None and port == diag_port and "MEMLOG" not in (
                    cur_title or ""
                ):
                    _set_window_title(hwnd, want_title)
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
    base_port: int = 5755,
    project_root: Path | str | None = None,
    diag_port: int | None = None,
) -> tuple[threading.Event, threading.Thread]:
    """Start a daemon thread that tiles BizHawk windows. Returns (stop, thread)."""
    all_monitors = list_monitors()
    target_monitors = pick_monitors(all_monitors, monitor)
    root = Path(project_root) if project_root is not None else None
    if diag_port is None:
        raw = os.environ.get("RE1_STEP_DIAG_PORT", "").strip()
        if raw.isdigit():
            diag_port = int(raw)
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
            "base_port": int(base_port),
            "project_root": root,
            "diag_port": diag_port,
        },
        name="bizhawk-grid-tiler",
        daemon=True,
    )
    thread.start()
    return stop, thread
