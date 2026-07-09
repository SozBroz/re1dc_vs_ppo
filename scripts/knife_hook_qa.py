"""Visible knife swing + live animation RAM hooks (hunt verification).

Uses RAM-gated execute_knife_macro (default): hold crouch aim until hooks
ready, then swing, then hold through recovery idle.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_hook_qa.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\knife_hook_qa.py --fixed-schedule
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
DEFAULT_STATE = ROOT / "states" / "jill_control_fresh.State"

from re1_rl.knife_macro import read_knife_hooks  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5780)
    ap.add_argument("--speed", type=int, default=100)
    ap.add_argument("--pause", type=float, default=3.0, help="seconds between swings")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument(
        "--fixed-schedule",
        action="store_true",
        help="use blind 42-frame schedule instead of RAM gates",
    )
    args = ap.parse_args()

    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import ACTION_NAMES, RE1Env

    port = int(args.port)
    bridge = BizHawkClient(
        port=port,
        timeout=300.0,
        screenshot_path=str(ROOT / "data" / f"_frame_{port}.png"),
    )
    bridge.start_server()
    print(f"[knife_hook_qa] launching EmuHawk on port {port} (watch the window)", flush=True)
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ],
        cwd=str(EMUHAWK.parent),
    )

    def shutdown(*_: object) -> None:
        print("[knife_hook_qa] stopping...", flush=True)
        try:
            env.close()
        except Exception:
            pass
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    env = RE1Env(
        curriculum_path=ROOT / "curriculum" / "m0_dining_to_main_hall.json",
        bridge=bridge,
        project_root=ROOT,
        async_cutscene_skip=False,
    )
    env._ram_skip.use_engine_patches = False
    env.knife_use_ram_gates = not args.fixed_schedule

    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))
        env.reset()

        mode = "fixed 42-frame" if args.fixed_schedule else "RAM-gated crouch aim"
        h0 = read_knife_hooks(bridge)
        print(
            f"[knife_hook_qa] {mode} | idle anim=0x{h0[0]:02X} aux=0x{h0[1]:02X} "
            f"recovery={h0[2]} | Ctrl+C to quit",
            flush=True,
        )

        noop = ACTION_NAMES.index("noop")
        knife = ACTION_NAMES.index("knife_swing")
        swing = 0
        while True:
            swing += 1
            for _ in range(4):
                env.step(noop)
            print(f"\n--- swing #{swing} ---", flush=True)
            _, _, _, _, info = env.step(knife)
            h1 = read_knife_hooks(bridge)
            print(
                f"  after macro: anim=0x{h1[0]:02X} aux=0x{h1[1]:02X} "
                f"recovery={h1[2]} hp={info.get('hp')}",
                flush=True,
            )
            time.sleep(float(args.pause))
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
