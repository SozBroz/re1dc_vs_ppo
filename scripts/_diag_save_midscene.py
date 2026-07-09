"""Capture a savestate in the middle of the Barry main-hall cutscene.

Fresh dining spawn sits on the 105->106 west double door. Open it, advance
a little into the door/cutscene span (bit80 clear), save the state. Feed the
result to play_human --start-savestate to exercise its auto-skip end to end.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_save_midscene.py --port 5713
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import GAME_STATE, ROOM_ID

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "states" / "jill_control_fresh.State"
OUT = ROOT / "states" / "_diag_barry_midscene.State"

POLL = [("state", GAME_STATE, "u32"), ("room", ROOM_ID, "u8")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5713)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)
    # proven recipe from test_ram_skip.py: hug the west wall, face the door,
    # press action
    b.step({"left": True}, 300)
    b.step({"up": True}, 30)
    b.step({"cross": True}, 4)
    b.frameadvance(10)
    v = b.read_ram(POLL)
    hi = (v["state"] >> 24) & 0xFF
    if hi & 0x80:
        print(f"FAILED to engage door: state=0x{v['state']:08X}", flush=True)
        b.quit(); b.close()
        return 1
    print(f"door engaged: state=0x{v['state']:08X}", flush=True)

    # advance further into the uncontrolled span, then save
    b.frameadvance(60)
    v = b.read_ram(POLL)
    hi = (v["state"] >> 24) & 0xFF
    print(f"mid-scene: state=0x{v['state']:08X} hi=0x{hi:02X} room={v['room']}", flush=True)
    if hi & 0x80:
        print("WARN: control already back; state may not exercise skip", flush=True)
    b.save_savestate(str(OUT))
    print(f"saved {OUT}", flush=True)

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
