"""Confirm 0x800C300B as the dialogue/modal discriminator across all phases.

Phases logged (fresh dining spawn):
  A. walking in control (dining)          expect bit80=1, flag=0x40
  B. Barry blood-check dialogue (modal)   expect bit80=1, flag=0x78
  C. dismissing the dialogue (cross mash)
  D. west door transition                 expect bit80=0
  E. Barry main-hall cutscene             expect bit80=0
  F. walking in control (main hall)       expect bit80=1, flag=0x20

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag2.py --port 5716
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import GAME_STATE, PLAYER_X, PLAYER_Z, ROOM_ID

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "states" / "jill_control_fresh.State"

FLAG = 0x800C300B

POLL = [
    ("state", GAME_STATE, "u32"),
    ("flag", FLAG, "u8"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]

samples: dict[str, Counter] = {}


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def log(phase: str, v: dict) -> None:
    hi = (v["state"] >> 24) & 0xFF
    samples.setdefault(phase, Counter())[(hi & 0x80 != 0, v["flag"])] += 1


def show() -> None:
    print("\n=== summary (in_control_bit, flag) -> count ===", flush=True)
    for phase, ctr in samples.items():
        line = ", ".join(f"(bit80={'1' if k[0] else '0'},flag=0x{k[1]:02X})x{n}"
                         for k, n in sorted(ctr.items(), key=lambda kv: -kv[1]))
        print(f"  {phase}: {line}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5716)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)

    # A: walk in control
    for mv in ("up", "left", "up", "right"):
        b.step({mv: True}, 16)
        log("A_control_dining", read(b))

    # B: trigger the Barry blood-check modal (same wander as flag hunt)
    moves = ["up", "up", "left", "up", "right", "up", "up", "down", "left", "up"] * 3
    modal_hit = False
    for i, mv in enumerate(moves):
        b.step({mv: True}, 24)
        b.step({"cross": True}, 2)
        b.frameadvance(20)
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        if hi & 0x80:
            before = v
            b.step({"up": True}, 12)
            after = read(b)
            if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) < 8:
                modal_hit = True
                print(f"modal at step {i}: flag=0x{v['flag']:02X} pos=({v['x']},{v['z']})",
                      flush=True)
                for _ in range(30):
                    log("B_modal", read(b))
                    b.frameadvance(4)
                break
            log("A_control_dining", after)
        else:
            log("D_door_or_scene", v)
            r = b.fast_forward(6000, mode_addr=0x800C3003, mask=0x80,
                               speed=6400, restore_speed=6400, invisible=False)
            print(f"unexpected uncontrolled span burned={r['burned']}", flush=True)
    if not modal_hit:
        print("WARN: no modal found; summary will lack phase B", flush=True)

    # C: dismiss dialogue, sampling as each text box closes
    for _ in range(20):
        b.step({"cross": True}, 2)
        b.frameadvance(12)
        v = read(b)
        log("C_dismissing", v)
        before = v
        b.step({"up": True}, 10)
        after = read(b)
        if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) > 16:
            print(f"dialogue dismissed: flag=0x{after['flag']:02X}", flush=True)
            break

    # D/E: west door + Barry main hall cutscene (walk left wall, up, action)
    b.step({"left": True}, 300)
    b.step({"up": True}, 30)
    b.step({"cross": True}, 4)
    for i in range(120):
        b.frameadvance(10)
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        if not hi & 0x80:
            log("DE_door_cutscene", v)
        else:
            log("F_posthall", v)
            if i > 20:
                break

    # F: prove control in main hall
    b.step({"up": True}, 16)
    v = read(b)
    log("F_posthall", v)
    print(f"final: room={v['room']} flag=0x{v['flag']:02X} pos=({v['x']},{v['z']})",
          flush=True)

    show()
    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
