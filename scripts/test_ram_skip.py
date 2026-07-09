"""Live test: RAM-hook door skip dining 105 -> main hall 106.

Launch EmuHawk first (same as training), then:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\test_ram_skip.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import GAME_MODE, IN_CONTROL_MASK, PLAYER_X, PLAYER_Z, ROOM_ID, STAGE_ID
from re1_rl.ram_skip import RamSkipper, room_code

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "states" / "jill_control.State"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5555,
                    help="bridge port (use a free one if training holds 5555)")
    ap.add_argument("--no-patches", action="store_true",
                    help="skip engine patches (baseline A/B measurement)")
    ap.add_argument("--chunk", type=int, default=16,
                    help="skip poll granularity in frames (small = precise burn count)")
    args = ap.parse_args()

    bridge = BizHawkClient(port=args.port, timeout=300.0)
    bridge.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    bridge.wait_for_client()
    bridge.set_speed(6400)

    skipper = RamSkipper(bridge)
    skipper.clear_engine_patches()

    bridge.load_savestate(str(CONTROL))
    bridge.frameadvance(5)
    bridge.step({"left": True}, 300)
    bridge.step({"up": True}, 30)
    pre = bridge.read_ram(
        [
            ("stage", STAGE_ID, "u8"),
            ("room", ROOM_ID, "u8"),
            ("x", PLAYER_X, "s16"),
            ("z", PLAYER_Z, "s16"),
            ("mode", GAME_MODE, "u8"),
        ]
    )
    print(f"pre-door: room={room_code(pre['stage'], pre['room'])} mode=0x{pre['mode']:02X}", flush=True)

    bridge.step({"cross": True}, 4)
    burned = skipper.skip_uncontrolled(chunk=args.chunk)
    post = bridge.read_ram(
        [
            ("stage", STAGE_ID, "u8"),
            ("room", ROOM_ID, "u8"),
            ("x", PLAYER_X, "s16"),
            ("z", PLAYER_Z, "s16"),
            ("mode", GAME_MODE, "u8"),
        ]
    )
    in_ctrl = bool(post["mode"] & IN_CONTROL_MASK)
    print(
        f"post-skip: room={room_code(post['stage'], post['room'])} "
        f"x={post['x']} z={post['z']} mode=0x{post['mode']:02X} "
        f"in_control={in_ctrl} burned={burned}",
        flush=True,
    )

    bridge.set_speed(100)
    bridge.quit()
    bridge.close()
    ok = room_code(post["stage"], post["room"]) == "106" and in_ctrl
    print("RAM_SKIP_PASS" if ok else "RAM_SKIP_FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
