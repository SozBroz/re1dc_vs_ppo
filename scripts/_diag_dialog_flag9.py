"""Map (bit80, msg_flag, ctl_flag) through the ENTIRE Barry main-hall scene.

Goal: find the signal covering the scripted spans where bit80 is SET and no
message window is open but the player still cannot act (the '50% speed tail'
the user reports). Candidate: 0x800CAB00 (MK 'Character Is Controllable',
0xFF when fully controllable).

Walks the fresh dining spawn through the west door (Barry scene), then logs
every flag-tuple change while advancing 4 frames at a time (cross tap when a
message box is open). Ends with movement probes.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag9.py --port 5725
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import (
    GAME_STATE,
    MESSAGE_FLAG,
    PLAYER_X,
    PLAYER_Z,
    ROOM_ID,
)

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "states" / "jill_control_fresh.State"

CTL_FLAG = 0x800CAB00

POLL = [
    ("state", GAME_STATE, "u32"),
    ("msg", MESSAGE_FLAG, "u8"),
    ("ctl", CTL_FLAG, "u8"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def key(v: dict) -> tuple:
    hi = (v["state"] >> 24) & 0xFF
    return (1 if hi & 0x80 else 0, 1 if v["msg"] & 0x80 else 0, v["ctl"], v["room"])


def fmt(v: dict) -> str:
    k = key(v)
    return (f"bit80={k[0]} msg={k[1]} ctl=0x{k[2]:02X} room={k[3]} "
            f"pos=({v['x']},{v['z']})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5725)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)
    print(f"spawn: {fmt(read(b))}", flush=True)

    # west door out of the dining room -> main hall Barry scene
    b.step({"left": True}, 300)
    b.step({"up": True}, 30)
    b.step({"cross": True}, 4)

    last = None
    span_start = 0
    f = 0
    for i in range(1500):  # up to 6000 frames
        v = read(b)
        k = key(v)
        if k != last:
            if last is not None:
                print(f"  f{span_start:5d}-f{f:5d} ({f - span_start:4d}f) "
                      f"bit80={last[0]} msg={last[1]} ctl=0x{last[2]:02X} "
                      f"room={last[3]}", flush=True)
            span_start = f
            last = k
        # tap cross while a message box is open so the scene advances
        if v["msg"] & 0x80:
            b.step({"cross": True}, 2)
            b.frameadvance(2)
            f += 4
        else:
            b.frameadvance(4)
            f += 4
        # stop once we've been fully back in control for a while
        if i > 100 and k[0] == 1 and k[1] == 0 and k[2] == 0xFF and f - span_start > 400:
            break
    print(f"  f{span_start:5d}-f{f:5d} (last) bit80={last[0]} msg={last[1]} "
          f"ctl=0x{last[2]:02X} room={last[3]}", flush=True)

    # ground truth: can we move?
    for d in ("up", "down", "left", "right"):
        before = read(b)
        b.step({d: True}, 12)
        after = read(b)
        moved = abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) >= 8
        print(f"probe {d}: moved={moved} {fmt(after)}", flush=True)
        if moved:
            break

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
