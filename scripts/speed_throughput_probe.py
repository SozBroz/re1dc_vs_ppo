"""Measure env steps/sec across BizHawk speedmode settings (one solo env).

Finds the knee where cranking speedmode stops paying because emulation
compute, screenshot round-trips, or Python overhead dominate.

Usage:
    python scripts/speed_throughput_probe.py --speeds 100 400 1600 3200 6400
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speeds", type=int, nargs="+",
                    default=[100, 400, 1600, 3200, 6400])
    ap.add_argument("--port", type=int, default=5830)
    ap.add_argument("--steps", type=int, default=60, help="timed env steps per speed")
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env

    port = int(args.port)
    bridge = BizHawkClient(
        port=port, timeout=300.0,
        screenshot_path=str(PROJECT_ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK), str(ROM), f"--lua={LUA}",
            "--socket_ip=127.0.0.1", f"--socket_port={port}",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        env = RE1Env(
            curriculum_path=PROJECT_ROOT / "curriculum" / "m0_dining_to_main_hall.json",
            bridge=bridge,
            project_root=PROJECT_ROOT,
            async_cutscene_skip=False,
        )
        env._ram_skip.use_engine_patches = False
        env.reset()
        noop = ACTION_NAMES.index("noop")
        knife = ACTION_NAMES.index("knife_swing")

        print(f"{'speed%':>7} {'steps/s':>8} {'ms/step':>8} {'knife ms':>9}", flush=True)
        for speed in args.speeds:
            bridge.set_speed(int(speed))
            for _ in range(5):  # warm
                env.step(noop)
            t0 = time.perf_counter()
            for _ in range(int(args.steps)):
                env.step(noop)
            dt = time.perf_counter() - t0
            # knife macro latency (42 frames vs 4)
            tk0 = time.perf_counter()
            env.step(knife)
            tk = (time.perf_counter() - tk0) * 1000
            sps = args.steps / dt
            print(f"{speed:>7} {sps:>8.1f} {1000 * dt / args.steps:>8.1f} {tk:>9.1f}",
                  flush=True)
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
