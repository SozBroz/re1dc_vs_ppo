"""Verify 0x800C300B as the in-control flag.

Reproduce the door transition (dining -> main hall), then every 30 frames:
log the flag AND probe responsiveness (tap up 10 frames, measure movement).
The flag is confirmed if flag!=0 exactly correlates with movement response.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

CONTROL = "D:/re1_rl/states/jill_control.State"
FIELDS = [
    ("x", 0x800C5158, "s16"),
    ("z", 0x800C5160, "s16"),
    ("room", 0x800C8661, "u8"),
    ("flag3003", 0x800C3003, "u8"),
    ("flag300B", 0x800C300B, "u8"),
]


def read(c: BizHawkClient) -> dict:
    return c.read_ram(FIELDS)


def main() -> int:
    c = BizHawkClient(timeout=300.0)
    c.start_server()
    print("listening; launch EmuHawk now", flush=True)
    c.wait_for_client()
    print("connected", flush=True)
    c.set_speed(6400)

    c.load_savestate(CONTROL)
    c.frameadvance(5)
    c.step({"left": True}, 300)
    c.step({"up": True}, 30)
    v = read(c)
    room0 = v["room"]
    print(f"pre-door: room={room0} 300B={v['flag300B']}", flush=True)
    c.step({"cross": True}, 4)

    # 40 probes x ~40 frames = ~26s emulated, covers anim + well beyond
    for i in range(40):
        before = read(c)
        c.step({"up": True}, 10)
        c.frameadvance(20)
        after = read(c)
        moved = abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) > 16
        print(f"  probe {i:2d}: room={after['room']} 3003={after['flag3003']:3d}"
              f" 300B={after['flag300B']:3d} moved={moved}", flush=True)

    c.set_speed(100)
    c.quit()
    c.close()
    print("VERIFY_FLAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
