"""Deterministically exit the dining room and log flag bytes through the
door transition.

Pattern per attempt: face a direction, walk until position stops changing
(wall/obstacle), tap cross (door prompt), watch room byte + flag candidates.
Rotates 90 deg between attempts. Turning rate ~6.4 angle-units/frame,
full circle = 4096.
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
    ("facing", 0x800C5198, "u16"),
    ("room", 0x800C8661, "u8"),
    ("flag3003", 0x800C3003, "u8"),
    ("flag300B", 0x800C300B, "u8"),
    ("oldroom", 0x800C8663, "u8"),
]

TURN_90 = 160  # frames of held turn ~= 1024 angle units


def read(client: BizHawkClient) -> dict:
    return client.read_ram(FIELDS)


def main() -> int:
    client = BizHawkClient(timeout=300.0)
    client.start_server()
    print("listening; launch EmuHawk now", flush=True)
    client.wait_for_client()
    print("connected", flush=True)
    client.set_speed(6400)
    client.load_savestate(CONTROL)
    client.frameadvance(5)

    start = read(client)
    print(f"spawn: {start}", flush=True)
    room0 = start["room"]

    # initial 180 turn (door likely behind spawn facing)
    client.step({"left": True}, TURN_90 * 2)

    for attempt in range(8):
        s = read(client)
        print(f"attempt {attempt}: facing={s['facing']} x={s['x']} z={s['z']}",
              flush=True)
        # walk until stuck (position delta < 32 across a 30-frame window)
        last = (s["x"], s["z"])
        for _ in range(20):
            client.step({"up": True}, 30)
            v = read(client)
            if v["room"] != room0:
                break
            cur = (v["x"], v["z"])
            if abs(cur[0] - last[0]) + abs(cur[1] - last[1]) < 32:
                break
            last = cur
        v = read(client)
        print(f"  stuck/stop at x={v['x']} z={v['z']} room={v['room']}", flush=True)

        # try the door: tap cross, then sample tightly for 300 frames
        client.step({"cross": True}, 4)
        for i in range(30):
            client.frameadvance(10)
            v = read(client)
            if i % 3 == 0 or v["room"] != room0:
                print(f"    t+{(i+1)*10:3d}f room={v['room']} flag3003={v['flag3003']}"
                      f" flag300B={v['flag300B']} oldroom={v['oldroom']}"
                      f" x={v['x']} z={v['z']}", flush=True)
            if v["room"] != room0:
                print("  ROOM CHANGED", flush=True)
                # trace flag bytes as the new room settles
                for j in range(20):
                    client.frameadvance(10)
                    v = read(client)
                    print(f"    settle+{(j+1)*10:3d}f room={v['room']}"
                          f" flag3003={v['flag3003']} flag300B={v['flag300B']}"
                          f" oldroom={v['oldroom']}", flush=True)
                client.set_speed(100)
                client.quit()
                client.close()
                print("DOOR_DONE", flush=True)
                return 0

        # no door here; rotate 90 and try again
        client.step({"left": True}, TURN_90)

    client.set_speed(100)
    client.quit()
    client.close()
    print("DOOR_DONE (no transition found)", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
