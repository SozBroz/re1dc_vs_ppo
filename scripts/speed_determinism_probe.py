"""Prove speedmode does not alter emulation: identical action script at two
speeds from the same savestate must produce identical per-step RAM traces.

Usage:
    python scripts/speed_determinism_probe.py --speeds 100 3200 --port 5820
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"

# Deterministic action script: movement, turns, knife swings.
# Indices per re1_rl.env.ACTION_NAMES.
ACTION_SCRIPT = (
    [0] * 2          # noop
    + [1] * 6        # forward
    + [3] * 3        # turn left
    + [1] * 4        # forward
    + [8]           # knife swing
    + [4] * 3        # turn right
    + [8]           # knife swing
    + [2] * 3        # backward
    + [8]           # knife swing
    + [0] * 2        # noop
)

TRACE_KEYS = ("x", "z", "facing", "hp", "room_id", "game_timer")


def run_trace(port: int, speed: int) -> list[dict]:
    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import RE1Env

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
    trace: list[dict] = []
    try:
        bridge.wait_for_client()
        bridge.set_speed(speed)
        env = RE1Env(
            curriculum_path=PROJECT_ROOT / "curriculum" / "m0_dining_to_main_hall.json",
            bridge=bridge,
            project_root=PROJECT_ROOT,
            async_cutscene_skip=False,
        )
        env._ram_skip.use_engine_patches = False
        env._ram_skip.training_speed = speed
        env._ram_skip.cutscene_speed = speed
        env.reset()
        for i, action in enumerate(ACTION_SCRIPT):
            _, _, _, _, info = env.step(action)
            st = info.get("state", {})
            row = {"step": i, "action": action}
            for k in TRACE_KEYS:
                row[k] = st.get(k)
            trace.append(row)
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        time.sleep(2.0)
    return trace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speeds", type=int, nargs="+", default=[100, 3200])
    ap.add_argument("--port", type=int, default=5820)
    args = ap.parse_args()

    traces: dict[int, list[dict]] = {}
    for i, speed in enumerate(args.speeds):
        print(f"[probe] running {len(ACTION_SCRIPT)}-step script at speed {speed}%...",
              flush=True)
        traces[speed] = run_trace(int(args.port) + i, speed)
        out = PROJECT_ROOT / "data" / f"determinism_trace_{speed}.json"
        out.write_text(json.dumps(traces[speed], indent=2), encoding="utf-8")
        print(f"[probe] wrote {out}", flush=True)

    speeds = list(traces)
    base = traces[speeds[0]]
    all_ok = True
    for other in speeds[1:]:
        diffs = []
        for a, b in zip(base, traces[other]):
            if a != b:
                diffs.append((a, b))
        if diffs:
            all_ok = False
            print(f"[probe] speed {speeds[0]} vs {other}: {len(diffs)} DIVERGENT steps:",
                  flush=True)
            for a, b in diffs[:10]:
                print(f"  step {a['step']} action={a['action']}", flush=True)
                print(f"    {speeds[0]:>5}%: {a}", flush=True)
                print(f"    {other:>5}%: {b}", flush=True)
        else:
            print(f"[probe] speed {speeds[0]} vs {other}: IDENTICAL "
                  f"({len(base)} steps, keys={TRACE_KEYS})", flush=True)
    print(f"[probe] verdict: {'DETERMINISTIC' if all_ok else 'DIVERGENT'}", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
