"""Profile the per-step cost breakdown of the env loop against live EmuHawk.

Times each bridge primitive separately over N reps so we know what the real
bottleneck is: emulation (step/frameadvance), screenshot IPC, or RAM reads.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from re1_rl.bizhawk_bridge import BizHawkClient

IN_STATE = "D:/re1_rl/states/jill_control.State"
N = 50


def bench(label: str, fn, n: int = N) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    arr = np.array(ts) * 1000
    print(f"  {label:<28} mean={arr.mean():7.2f}ms  p50={np.percentile(arr,50):7.2f}"
          f"  p95={np.percentile(arr,95):7.2f}", flush=True)
    return float(arr.mean())


def main() -> int:
    client = BizHawkClient(timeout=300.0)
    client.start_server()
    print("listening; launch EmuHawk now", flush=True)
    client.wait_for_client()
    print("connected; profiling at 6400% speed", flush=True)
    client.set_speed(6400)
    client.load_savestate(IN_STATE)
    client.frameadvance(5)

    total = {}
    total["ping (protocol overhead)"] = bench("ping (protocol overhead)", lambda: client.ping())
    total["read_ram (13 fields)"] = bench("read_ram (13 fields)", lambda: client.read_ram())
    total["step 8 frames + buttons"] = bench("step 8 frames + buttons",
                                             lambda: client.step({"up": True}, 8))
    total["screenshot (PNG via disk)"] = bench("screenshot (PNG via disk)",
                                               lambda: client.screenshot())
    total["loadstate"] = bench("loadstate", lambda: client.load_savestate(IN_STATE), n=15)

    step_cost = (total["step 8 frames + buttons"] + total["screenshot (PNG via disk)"]
                 + total["read_ram (13 fields)"])
    print(f"\nper-env-step (step+shot+ram): {step_cost:.1f}ms -> {1000/step_cost:.1f} steps/s"
          f" -> {8000/step_cost:.0f} emulated fps", flush=True)

    client.set_speed(100)
    client.quit()
    client.close()
    print("PROFILE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
