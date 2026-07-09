"""Validate 0x800C8665 (bit 0x80 = message/dialogue window open).

Checks every phase:
  idle in control        -> expect 0x00
  walking in control     -> expect 0x00
  examine modal open     -> expect 0x80
  modal dismissed        -> expect 0x00
  door transition        -> log value
  aiming (r1)            -> expect 0x00  (must not false-positive)

Triggers TWO different modals (dining item examine + tea room Kenneth blood)
to make sure the bit isn't scene-specific.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag8.py --port 5722
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

MSG_FLAG = 0x800C8665

POLL = [
    ("state", GAME_STATE, "u32"),
    ("msg", MSG_FLAG, "u8"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]

failures: list[str] = []


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def fmt(v: dict) -> str:
    hi = (v["state"] >> 24) & 0xFF
    return (f"bit80={'1' if hi & 0x80 else '0'} msg=0x{v['msg']:02X} "
            f"room={v['room']} pos=({v['x']},{v['z']})")


def expect(phase: str, v: dict, want_msg_bit: bool) -> None:
    got = bool(v["msg"] & 0x80)
    ok = got == want_msg_bit
    tag = "OK " if ok else "FAIL"
    print(f"[{tag}] {phase}: {fmt(v)}", flush=True)
    if not ok:
        failures.append(phase)


def all_dirs_dead(b: BizHawkClient) -> tuple[bool, dict]:
    last = read(b)
    for d in ("up", "down", "left", "right"):
        before = read(b)
        b.step({d: True}, 12)
        after = read(b)
        last = after
        if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) >= 8:
            return False, last
    return True, last


def hunt_modal(b: BizHawkClient, tag: str) -> bool:
    moves = ["up", "up", "left", "up", "right", "up", "up", "down", "left", "up"] * 3
    for i, mv in enumerate(moves):
        b.step({mv: True}, 24)
        b.step({"cross": True}, 2)
        b.frameadvance(20)
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        if not hi & 0x80:
            print(f"    door/scene during hunt: {fmt(v)}", flush=True)
            r = b.fast_forward(6000, mode_addr=0x800C3003, mask=0x80,
                               speed=6400, restore_speed=6400, invisible=False)
            print(f"    burned {r['burned']}", flush=True)
            continue
        dead, last = all_dirs_dead(b)
        if dead:
            expect(f"{tag}: modal open", read(b), True)
            for _ in range(4):
                b.frameadvance(10)
                expect(f"{tag}: modal idle", read(b), True)
            # dismiss
            for j in range(25):
                b.step({"cross": True}, 8)
                b.frameadvance(20)
                dead2, last2 = all_dirs_dead(b)
                if not dead2:
                    b.frameadvance(10)
                    expect(f"{tag}: dismissed", read(b), False)
                    return True
            print(f"    {tag}: FAILED to dismiss", flush=True)
            failures.append(f"{tag}: dismiss")
            return True
    print(f"    {tag}: no modal found", flush=True)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5722)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)

    expect("idle control", read(b), False)
    b.step({"up": True}, 16)
    expect("walking", read(b), False)
    b.step({"r1": True}, 16)
    expect("aiming", read(b), False)
    b.step({}, 4)

    hunt_modal(b, "modal#1")

    # keep wandering for a second, different modal (or door -> other room)
    hunt_modal(b, "modal#2")

    print(f"\nRESULT: {'ALL OK' if not failures else 'FAILURES: ' + ', '.join(failures)}",
          flush=True)
    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
