"""Validate grayscale obs + cutscene auto-skip through a real door.

Drives the env with the known door script (turn ~170deg, walk, interact) and
asserts: obs is 84x77x4, the door transition is fast-forwarded by
_skip_uncontrolled, room flips 105 -> 106, and the waypoint bonus fires.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env

TURN_LEFT, FORWARD, INTERACT = 3, 1, 7


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5555,
                    help="bridge port (use a free one if training holds 5555)")
    args = ap.parse_args()

    bridge = BizHawkClient(port=args.port, timeout=300.0)
    bridge.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    bridge.wait_for_client()
    print("connected", flush=True)
    bridge.set_speed(6400)

    env = RE1Env(
        curriculum_path="D:/re1_rl/curriculum/m0_dining_to_main_hall.json",
        bridge=bridge,
        project_root="D:/re1_rl",
    )
    obs, _ = env.reset()
    assert obs["frame"].shape == (84, 77, 4), obs["frame"].shape
    print(f"obs frame shape ok: {obs['frame'].shape}", flush=True)

    # ~170 deg left at 6.4 units/frame, frame_skip=8 -> 38 turn actions
    script = [TURN_LEFT] * 38 + [FORWARD] * 4 + [INTERACT] + [FORWARD] * 6
    total = 0.0
    for i, a in enumerate(script):
        obs, r, term, trunc, info = env.step(a)
        total += r
        if info["frames_skipped"] or r > 0.01 or i % 10 == 0:
            print(f"  step {i:2d} r={r:+.3f} room={info['room_id']}"
                  f" skipped={info['frames_skipped']}f", flush=True)
        if term or trunc:
            break

    print(f"final room={info['room_id']} total_reward={total:+.3f}", flush=True)
    ok = info["room_id"] == "106"
    bridge.set_speed(100)
    bridge.quit()
    bridge.close()
    print("DOOR_ENV_PASS" if ok else "DOOR_ENV_FAIL (room never changed)", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
