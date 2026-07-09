"""Pin down 0x800C300B (+neighbors) on the TRUE dining blood-check modal.

Replicates _diag_dialog_flag.py's exact wander (which found the modal at
room 5 pos~(30192,2693)) and samples 0x800C3008..0x800C300B every phase:
walking, modal open, each dismiss tap, walking after.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag5.py --port 5719
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
    ("s2", 0x800C3008, "u32"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def fmt(v: dict) -> str:
    hi = (v["state"] >> 24) & 0xFF
    b3 = (v["s2"] >> 24) & 0xFF
    return (f"bit80={'1' if hi & 0x80 else '0'} s2=0x{v['s2']:08X} "
            f"[300B]=0x{b3:02X} room={v['room']} pos=({v['x']},{v['z']})")


def movement_dead(b: BizHawkClient) -> tuple[bool, dict]:
    before = read(b)
    b.step({"up": True}, 12)
    after = read(b)
    return (abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) < 8, after)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5719)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)
    print(f"start:   {fmt(read(b))}", flush=True)

    # EXACT wander from _diag_dialog_flag.py (same savestate => deterministic)
    moves = ["up", "up", "left", "up", "right", "up", "up", "down", "left", "up"] * 3
    for i, mv in enumerate(moves):
        b.step({mv: True}, 24)
        b.step({"cross": True}, 2)
        b.frameadvance(20)
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        if not hi & 0x80:
            print(f"[{i}] uncontrolled span hit: {fmt(v)}", flush=True)
            r = b.fast_forward(6000, mode_addr=0x800C3003, mask=0x80,
                               speed=6400, restore_speed=6400, invisible=False)
            print(f"    burned={r['burned']}", flush=True)
            continue
        print(f"[{i}] walk: {fmt(v)}", flush=True)
        dead, after = movement_dead(b)
        if dead:
            print(f"[{i}] MODAL: {fmt(after)}", flush=True)
            for j in range(8):
                b.frameadvance(8)
                print(f"    modal idle {j}: {fmt(read(b))}", flush=True)
            for j in range(20):
                b.step({"cross": True}, 2)
                b.frameadvance(12)
                v = read(b)
                print(f"    dismiss tap {j}: {fmt(v)}", flush=True)
                dead2, after2 = movement_dead(b)
                if not dead2:
                    print(f"    dismissed -> {fmt(after2)}", flush=True)
                    break
            break

    for mv in ("up", "left", "down", "right"):
        b.step({mv: True}, 16)
        print(f"post: {fmt(read(b))}", flush=True)

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
