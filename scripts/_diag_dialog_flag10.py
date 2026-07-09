"""Probe the 'all flags say control' spans inside the Barry scene.

_diag_dialog_flag9 found spans (e.g. ~276 frames) where bit80=1, msg=0,
ctl=0xFF while the scripted scene still runs. This script enters the scene,
and each time that flag tuple appears it probes movement immediately:
  - if movement works -> the span is REAL control (harness behavior correct)
  - if movement is dead -> snapshot RAM for a diff against true control

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag10.py --port 5726
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

REGIONS = [
    (0x800C2000, 0x8000),
    (0x800CF000, 0x1000),
]

POLL = [
    ("state", GAME_STATE, "u32"),
    ("msg", MESSAGE_FLAG, "u8"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def flags(v: dict) -> tuple[int, int]:
    hi = (v["state"] >> 24) & 0xFF
    return (1 if hi & 0x80 else 0, 1 if v["msg"] & 0x80 else 0)


def snap(b: BizHawkClient) -> dict[int, int]:
    out: dict[int, int] = {}
    for base, size in REGIONS:
        for off in range(0, size, 0x1000):
            blk = b.read_block(base + off, min(0x1000, size - off))
            for i, byte in enumerate(blk):
                out[base + off + i] = byte
    return out


def try_move(b: BizHawkClient) -> bool:
    before = read(b)
    b.step({"down": True}, 10)
    after = read(b)
    return abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) >= 8


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5726)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)

    # true-control baseline in the dining room (idle, no input)
    base_snaps: dict[int, set[int]] = {}
    for _ in range(6):
        b.frameadvance(10)
        for a, v in snap(b).items():
            base_snaps.setdefault(a, set()).add(v)
    print("baseline captured", flush=True)

    # walk into the Barry scene
    b.step({"left": True}, 300)
    b.step({"up": True}, 30)
    b.step({"cross": True}, 4)

    frozen_snaps: dict[int, set[int]] = {}
    n_frozen = 0
    f = 0
    while f < 6000:
        v = read(b)
        b80, msg = flags(v)
        if not b80:
            b.frameadvance(4)
            f += 4
            continue
        if msg:
            b.step({"cross": True}, 2)
            b.frameadvance(2)
            f += 4
            continue
        # all flags say control: ground-truth it
        moved = try_move(b)
        f += 10
        if moved:
            print(f"f{f}: control-span CONFIRMED REAL (moved) room={v['room']}",
                  flush=True)
            break
        n_frozen += 1
        print(f"f{f}: FROZEN despite control flags room={v['room']} "
              f"pos=({v['x']},{v['z']})", flush=True)
        for a, val in snap(b).items():
            frozen_snaps.setdefault(a, set()).add(val)
        b.frameadvance(20)
        f += 20
        if n_frozen >= 8:
            break

    if n_frozen:
        hits = []
        for a, fs in frozen_snaps.items():
            if len(fs) != 1:
                continue
            fv = next(iter(fs))
            bs = base_snaps.get(a, set())
            if bs and fv not in bs and len(bs) <= 2:
                hits.append((a, sorted(bs), fv))
        hits.sort()
        print(f"\n=== {len(hits)} frozen-span discriminators ===", flush=True)
        for a, bs, fv in hits[:80]:
            bs_txt = ",".join(f"0x{x:02X}" for x in bs)
            print(f"  0x{a:08X}: control={{{bs_txt}}} frozen=0x{fv:02X}", flush=True)
    else:
        print("no frozen control-flag spans encountered", flush=True)

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
