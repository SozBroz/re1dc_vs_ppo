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
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.window_grid import build_slots, list_monitors, pick_monitors, start_grid_tiler
PYTHON = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)


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

    all_monitors = list_monitors()
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

    stop, tiler = start_grid_tiler(
        expected=args.count,
        cols=args.cols,
        rows=args.rows,
        gap=args.gap,
        monitor=args.monitor,
        lock_windows=not args.no_lock_windows,
    )

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
        "--no-headless",
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
