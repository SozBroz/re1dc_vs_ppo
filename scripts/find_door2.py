"""Open the dining->main hall door: it is directly behind Jill's spawn
(oldroom=6 proves she entered through it). Try cross at spawn facing, then
180-turn variants with increasing walk-in distances."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

CONTROL = "D:/re1_rl/states/jill_control.State"
FIELDS = [
    ("x", 0x800C5158, "s16"),
    ("z", 0x800C5160, "s16"),
    ("facing", 0x800C5198, "u16"),
    ("room", 0x800C8661, "u8"),
    ("flag3003", 0x800C3003, "u8"),
    ("flag300B", 0x800C300B, "u8"),
    ("oldroom", 0x800C8663, "u8"),
]


def read(c: BizHawkClient) -> dict:
    return c.read_ram(FIELDS)


def attempt(c: BizHawkClient, label: str, turn_frames: int, walk_frames: int) -> bool:
    c.load_savestate(CONTROL)
    c.frameadvance(5)
    if turn_frames:
        c.step({"left": True}, turn_frames)
    if walk_frames:
        c.step({"up": True}, walk_frames)
    v = read(c)
    room0 = v["room"]
    c.step({"cross": True}, 4)
    # sample the transition tightly: every 5 frames for 400 frames
    trace = []
    for i in range(80):
        c.frameadvance(5)
        v = read(c)
        trace.append(v)
        if v["room"] != room0:
            print(f"{label}: DOOR OPENED (facing={trace[0]['facing']})", flush=True)
            for k, t in enumerate(trace[-6:]):
                print(f"   f{(i-5+k)*5}: room={t['room']} 3003={t['flag3003']}"
                      f" 300B={t['flag300B']} oldroom={t['oldroom']}", flush=True)
            for j in range(24):
                c.frameadvance(10)
                v = read(c)
                if j % 3 == 0:
                    print(f"   settle+{(j+1)*10}: room={v['room']} 3003={v['flag3003']}"
                          f" 300B={v['flag300B']} oldroom={v['oldroom']}"
                          f" x={v['x']} z={v['z']}", flush=True)
            return True
    v = trace[-1]
    print(f"{label}: no (facing={trace[0]['facing']} x={v['x']} z={v['z']}"
          f" 3003={v['flag3003']} 300B={v['flag300B']})", flush=True)
    return False


def main() -> int:
    c = BizHawkClient(timeout=300.0)
    c.start_server()
    print("listening; launch EmuHawk now", flush=True)
    c.wait_for_client()
    print("connected", flush=True)
    c.set_speed(6400)

    variants = [
        ("cross at spawn", 0, 0),
        ("180 + cross", 320, 0),
        ("180 + walk15 + cross", 320, 15),
        ("180 + walk30 + cross", 320, 30),
        ("180 + walk50 + cross", 320, 50),
        ("170deg + walk30 + cross", 300, 30),
        ("190deg + walk30 + cross", 340, 30),
    ]
    for label, t, w in variants:
        if attempt(c, label, t, w):
            break

    c.set_speed(100)
    c.quit()
    c.close()
    print("DOOR2_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
