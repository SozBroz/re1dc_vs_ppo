"""Capture a PNG per emulated frame of the knife macro (animation QA).

Steps the macro schedule one frame at a time through the bridge and
screenshots after each frame, so knife animation quality can be judged
frame-by-frame without enemies or reward metrics.

Usage:
    python scripts/knife_frame_capture.py
    python scripts/knife_frame_capture.py --aim 5 --swing 5 --recovery 11 --scale 2
Output: data/knife_capture/frame_XX.png
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"
DEFAULT_PORT = 5779
OUT_DIR = PROJECT_ROOT / "data" / "knife_capture"


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-frame knife macro capture")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--noops", type=int, default=4)
    ap.add_argument("--aim", type=int, default=None)
    ap.add_argument("--swing", type=int, default=None)
    ap.add_argument("--recovery", type=int, default=None)
    ap.add_argument("--scale", type=int, default=None)
    ap.add_argument("--tail", type=int, default=12, help="extra released frames captured after macro")
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env
    from re1_rl.knife_macro import build_knife_frame_buttons

    kwargs = {}
    for key in ("aim", "swing", "recovery", "scale"):
        v = getattr(args, key)
        if v is not None:
            kwargs[key] = int(v)
    schedule = build_knife_frame_buttons(**kwargs)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("frame_*.png"):
        old.unlink()

    port = int(args.port)
    bridge = BizHawkClient(
        port=port, timeout=300.0,
        screenshot_path=str(PROJECT_ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)

        env = RE1Env(
            curriculum_path=PROJECT_ROOT / "curriculum" / "m0_dining_to_main_hall.json",
            bridge=bridge,
            project_root=PROJECT_ROOT,
            async_cutscene_skip=False,
        )
        env._ram_skip.use_engine_patches = False
        env.reset()
        noop = ACTION_NAMES.index("noop")
        for _ in range(int(args.noops)):
            env.step(noop)

        print(f"[capture] stepping {len(schedule)} macro frames + {args.tail} tail", flush=True)
        for i, btn in enumerate(schedule, start=1):
            bridge.step(n=1, sticky={}, frame_buttons=[btn])
            bridge.screenshot(str(OUT_DIR / f"frame_{i:02d}.png"))
        for j in range(1, int(args.tail) + 1):
            bridge.step(n=1, sticky={}, frame_buttons=[{}])
            bridge.screenshot(str(OUT_DIR / f"frame_{len(schedule) + j:02d}.png"))
        print(f"[capture] wrote {len(schedule) + int(args.tail)} PNGs to {OUT_DIR}", flush=True)
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
