"""Live test: Wesker/Barry main-hall intro cutscene skip from jill_start.State.

jill_start.State is saved BEFORE the mansion intro conversation, so loading it
leaves the game uncontrolled for thousands of frames. Verifies that
skip_uncontrolled (dialogue taps + cutscene-turbo patch + invisible
fast-forward) burns the whole scene and hands back control within the cap.

Run (server first, then EmuHawk with matching --socket_port):
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\test_cutscene_skip.py --port 5703
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import GAME_MODE, IN_CONTROL_MASK, ROOM_ID, STAGE_ID
from re1_rl.ram_skip import RamSkipper, room_code

ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "states" / "jill_start.State"

POLL = [("stage", STAGE_ID, "u8"), ("room", ROOM_ID, "u8"), ("mode", GAME_MODE, "u8")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5703)
    ap.add_argument("--no-patches", action="store_true")
    args = ap.parse_args()

    bridge = BizHawkClient(port=args.port, timeout=300.0)
    bridge.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    bridge.wait_for_client()
    bridge.set_speed(6400)

    skipper = RamSkipper(bridge)
    if not args.no_patches:
        skipper.install_engine_patches()

    bridge.load_savestate(str(START))
    bridge.frameadvance(2)
    pre = bridge.read_ram(POLL)
    print(
        f"pre-skip: room={room_code(pre['stage'], pre['room'])} "
        f"mode=0x{pre['mode']:02X} in_control={bool(pre['mode'] & IN_CONTROL_MASK)}",
        flush=True,
    )

    t0 = time.perf_counter()
    burned = skipper.skip_uncontrolled()
    wall = time.perf_counter() - t0
    post = bridge.read_ram(POLL)
    in_ctrl = bool(post["mode"] & IN_CONTROL_MASK)
    print(
        f"post-skip: room={room_code(post['stage'], post['room'])} "
        f"mode=0x{post['mode']:02X} in_control={in_ctrl} "
        f"burned={burned} wall={wall:.1f}s",
        flush=True,
    )

    bridge.set_speed(100)
    bridge.quit()
    bridge.close()
    print("CUTSCENE_SKIP_PASS" if in_ctrl else "CUTSCENE_SKIP_FAIL", flush=True)
    return 0 if in_ctrl else 1


if __name__ == "__main__":
    raise SystemExit(main())
