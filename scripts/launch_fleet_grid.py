"""Launch N parallel RE1 training agents and tile BizHawk windows across monitors.

Usage:
    python scripts/launch_fleet_grid.py --count 18
    python scripts/launch_fleet_grid.py --count 18 --cols 3 --rows 2 --dry-run

Default layout: 6 windows per monitor (3x2) on up to 3 detected displays.

Pin all windows to one display:
    python scripts/launch_fleet_grid.py --count 12 --monitor right --cols 4 --rows 3
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import mss

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)

user32 = ctypes.windll.user32
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

TITLE_NEEDLES = ("bizhawk", "emuhawk", "resident evil")


def _monitors() -> list[dict[str, int]]:
    with mss.MSS() as sct:
        return [
            {
                "left": int(m["left"]),
                "top": int(m["top"]),
                "width": int(m["width"]),
                "height": int(m["height"]),
            }
            for m in sct.monitors[1:]
        ]


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
    raise ValueError(f"unknown --monitor {which!r}; use left|center|right|N|all")


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


def tile_loop(
    *,
    expected: int,
    monitors: list[dict[str, int]],
    cols: int,
    rows: int,
    gap: int,
    stop: threading.Event,
    interval: float,
    lock_windows: bool,
) -> None:
    slots = build_slots(expected, monitors, cols=cols, rows=rows, gap=gap)
    placed: dict[int, int] = {}
    per_monitor = cols * rows
    lock_note = ", lock=on" if lock_windows else ""
    print(
        f"[fleet-grid] tiling up to {expected} windows — "
        f"{len(monitors)} target monitor(s), {cols}x{rows} grid, gap={gap}px{lock_note}",
        flush=True,
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
            print(
                f"[fleet-grid] window {slot_idx + 1}/{expected} "
                f"monitor {mon_idx + 1} slot ({local % cols},{local // cols}) — {title!r}",
                flush=True,
            )
        if len(placed) >= expected and not initial_done:
            print("[fleet-grid] all windows placed", flush=True)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=18, help="parallel env count")
    ap.add_argument("--monitor", default="all",
                    help="tile target: left, center, right, 1-based index, or all (default)")
    ap.add_argument("--cols", type=int, default=3, help="columns per monitor")
    ap.add_argument("--rows", type=int, default=2, help="rows per monitor")
    ap.add_argument("--gap", type=int, default=8, help="pixel gap between tiles")
    ap.add_argument("--base-port", type=int, default=5555)
    ap.add_argument("--total-steps", type=int, default=0,
                    help="training timesteps (0 = no limit, run until interrupted)")
    ap.add_argument("--curriculum", default="curriculum/m0_dining_to_main_hall.json")
    ap.add_argument("--resume", default=None)
    ap.add_argument("--fresh", action="store_true",
                    help="new random policy; do not auto-load old checkpoints")
    ap.add_argument("--run-name", default=None,
                    help="isolate checkpoints/tb under data/checkpoints/<name>/")
    ap.add_argument("--training-speed", type=int, default=3200)
    ap.add_argument("--skip-chunk", type=int, default=600)
    ap.add_argument("--capture-checkpoints", action="store_true")
    ap.add_argument("--tile-only", action="store_true",
                    help="only tile existing BizHawk windows, do not start training")
    ap.add_argument(
        "--no-lock-windows",
        action="store_true",
        help="stop enforcing grid size/position after initial tile (default: keep locked)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    all_monitors = _monitors()
    try:
        target_monitors = pick_monitors(all_monitors, args.monitor)
    except ValueError as exc:
        print(f"[fleet-grid] error: {exc}", flush=True)
        return 2

    per_mon = args.cols * args.rows
    print(
        f"[fleet-grid] {args.count} agents -> "
        f"{(args.count + per_mon - 1) // per_mon} monitor band(s) "
        f"({per_mon} tiles/monitor, {len(all_monitors)} display(s) detected)",
        flush=True,
    )
    target_keys = {
        (m["left"], m["top"], m["width"], m["height"]) for m in target_monitors
    }
    for i, m in enumerate(all_monitors, 1):
        key = (m["left"], m["top"], m["width"], m["height"])
        tag = "  <-- target" if key in target_keys else ""
        print(
            f"  monitor {i}: {m['width']}x{m['height']} @ ({m['left']},{m['top']}){tag}",
            flush=True,
        )

    if args.dry_run:
        try:
            slots = build_slots(
                args.count, target_monitors, cols=args.cols, rows=args.rows, gap=args.gap
            )
        except ValueError as exc:
            print(f"[fleet-grid] error: {exc}", flush=True)
            return 2
        for i, (x, y, w, h) in enumerate(slots):
            mon = (i // per_mon) % len(target_monitors)
            print(f"  slot {i:02d}: target monitor {mon + 1} ({x},{y}) {w}x{h}")
        return 0

    stop = threading.Event()
    tiler = threading.Thread(
        target=tile_loop,
        kwargs={
            "expected": args.count,
            "monitors": target_monitors,
            "cols": args.cols,
            "rows": args.rows,
            "gap": args.gap,
            "stop": stop,
            "interval": 1.5,
            "lock_windows": not args.no_lock_windows,
        },
        daemon=True,
    )
    tiler.start()

    if args.tile_only:
        try:
            while tiler.is_alive():
                tiler.join(timeout=1.0)
        except KeyboardInterrupt:
            stop.set()
        return 0

    cmd = [
        str(PYTHON),
        str(PROJECT_ROOT / "scripts" / "train_parallel.py"),
        "--n-envs",
        str(args.count),
        "--total-steps",
        str(args.total_steps),
        "--curriculum",
        args.curriculum,
        "--base-port",
        str(args.base_port),
        "--training-speed",
        str(args.training_speed),
        "--skip-chunk",
        str(args.skip_chunk),
    ]
    if args.resume:
        cmd.extend(["--resume", args.resume])
    if args.fresh:
        cmd.append("--fresh")
    if args.run_name:
        cmd.extend(["--run-name", args.run_name])
    if args.capture_checkpoints:
        cmd.append("--capture-checkpoints")

    print(f"[fleet-grid] launching: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("[fleet-grid] interrupt — terminating training fleet", flush=True)
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        stop.set()
        tiler.join(timeout=3.0)
    return int(proc.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
