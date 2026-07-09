"""Find the RAM flag for modal dialogue/message windows (bit80 stays SET).

Talking to Barry / examining objects opens a message box that freezes the
player but does NOT clear GAME_MODE bit 0x80 -- so the auto-skip is blind.
This script walks around the fresh dining room pressing action until a modal
appears (bit80 set but movement dead), then diffs RAM blocks captured
in-control vs in-dialogue to expose a discriminator byte.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag.py --port 5715
  (then launch EmuHawk with --socket_port=5715)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import GAME_STATE, PLAYER_X, PLAYER_Z, ROOM_ID

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "states" / "jill_control_fresh.State"

POLL = [
    ("state", GAME_STATE, "u32"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]

# candidate flag regions: around GAME_STATE, and the 0x800C86xx globals bank
BLOCKS = [
    (0x800C2FF0, 0x60),
    (0x800C8650, 0x80),
]


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def fmt(v: dict) -> str:
    hi = (v["state"] >> 24) & 0xFF
    return (f"state=0x{v['state']:08X} hi=0x{hi:02X} room={v['room']} "
            f"pos=({v['x']},{v['z']})")


def dump_blocks(b: BizHawkClient) -> dict[int, list[int]]:
    return {addr: b.read_block(addr, n) for addr, n in BLOCKS}


def diff_blocks(a: dict[int, list[int]], c: dict[int, list[int]], label: str) -> None:
    print(f"--- diff {label} ---", flush=True)
    for addr in a:
        for i, (x, y) in enumerate(zip(a[addr], c[addr])):
            if x != y:
                print(f"  0x{addr + i:08X}: 0x{x:02X} -> 0x{y:02X}", flush=True)


def movement_dead(b: BizHawkClient) -> bool:
    before = read(b)
    b.step({"up": True}, 12)
    after = read(b)
    return abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) < 8


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5715)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)
    print(f"start: {fmt(read(b))}", flush=True)
    base = dump_blocks(b)

    # wander pattern: forward bursts with action presses; detect modal text
    found_modal = False
    moves = ["up", "up", "left", "up", "right", "up", "up", "down", "left", "up"] * 3
    for i, mv in enumerate(moves):
        b.step({mv: True}, 24)
        b.step({"cross": True}, 2)
        b.frameadvance(20)
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        if not hi & 0x80:
            print(f"[{i}] bit80 cleared (door/cutscene): {fmt(v)} -- skipping on", flush=True)
            # burn through it (no patches needed for diagnosis)
            r = b.fast_forward(6000, mode_addr=0x800C3003, mask=0x80,
                               speed=6400, restore_speed=6400, invisible=False)
            print(f"    burned={r['burned']} -> {fmt(read(b))}", flush=True)
            continue
        if movement_dead(b):
            v = read(b)
            print(f"[{i}] MODAL detected (bit80 set, movement dead): {fmt(v)}", flush=True)
            modal = dump_blocks(b)
            diff_blocks(base, modal, "control -> modal")
            found_modal = True
            # dismiss and re-diff to confirm the flag resets
            for _ in range(6):
                b.step({"cross": True}, 2)
                b.frameadvance(16)
                if not movement_dead(b):
                    break
            after = dump_blocks(b)
            diff_blocks(modal, after, "modal -> dismissed")
            break

    if not found_modal:
        print("no modal found on wander path", flush=True)

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
