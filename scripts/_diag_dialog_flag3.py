"""Verify PS1 candidates for 'Character Is Controllable' + text counter.

MediaKite CE table -> PS1 linear map (MK - 0x833090 + 0x800C3000), verified
exact on X/HP/room/characterID anchors:
  Character Is Controllable  MK 0x83AB90 -> PS1 0x800CAB00
  Text char counter          MK 0x83F8F0 -> PS1 0x800CF860
  Current Message buffer     MK 0x83F8EC -> PS1 0x800CF85C

Samples all candidates in: control walk, Barry blood modal, door/cutscene,
post-scene control. A good flag separates modal from control.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag3.py --port 5717
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

CTRL_FLAG = 0x800CAB00   # MK 'Character Is Controllable'
TEXT_CTR = 0x800CF860    # MK 'Text char counter'
MSG_BUF = 0x800CF85C     # MK 'Current Message'

POLL = [
    ("state", GAME_STATE, "u32"),
    ("ctl", CTRL_FLAG, "u8"),
    ("txt", TEXT_CTR, "u8"),
    ("msg", MSG_BUF, "u32"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]

samples: dict[str, Counter] = {}


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def fmt(v: dict) -> str:
    hi = (v["state"] >> 24) & 0xFF
    return (f"bit80={'1' if hi & 0x80 else '0'} ctl=0x{v['ctl']:02X} "
            f"txt=0x{v['txt']:02X} msg=0x{v['msg']:08X} room={v['room']} "
            f"pos=({v['x']},{v['z']})")


def log(phase: str, v: dict) -> None:
    hi = (v["state"] >> 24) & 0xFF
    samples.setdefault(phase, Counter())[
        (hi & 0x80 != 0, v["ctl"], v["txt"] != 0)
    ] += 1


def show() -> None:
    print("\n=== summary (bit80, ctl_flag, text_active) -> count ===", flush=True)
    for phase, ctr in samples.items():
        line = ", ".join(
            f"(bit80={'1' if k[0] else '0'},ctl=0x{k[1]:02X},txt={'Y' if k[2] else 'n'})x{n}"
            for k, n in sorted(ctr.items(), key=lambda kv: -kv[1]))
        print(f"  {phase}: {line}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5717)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)
    print(f"start: {fmt(read(b))}", flush=True)

    # A: control walk
    for mv in ("up", "left", "up", "right"):
        b.step({mv: True}, 16)
        log("A_control", read(b))

    # B: hunt the Barry blood modal (wander + action)
    moves = ["up", "up", "left", "up", "right", "up", "up", "down", "left", "up"] * 3
    for i, mv in enumerate(moves):
        b.step({mv: True}, 24)
        b.step({"cross": True}, 2)
        b.frameadvance(20)
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        if not hi & 0x80:
            log("D_uncontrolled", v)
            r = b.fast_forward(6000, mode_addr=0x800C3003, mask=0x80,
                               speed=6400, restore_speed=6400, invisible=False)
            print(f"burned uncontrolled span: {r['burned']}", flush=True)
            continue
        before = v
        b.step({"up": True}, 12)
        after = read(b)
        if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) < 8:
            print(f"MODAL at step {i}: {fmt(after)}", flush=True)
            for _ in range(20):
                log("B_modal", read(b))
                b.frameadvance(4)
            break
        log("A_control", after)

    # C: dismiss the modal
    for _ in range(20):
        b.step({"cross": True}, 2)
        b.frameadvance(12)
        v = read(b)
        before = v
        b.step({"up": True}, 10)
        after = read(b)
        if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) > 16:
            log("C_after_dismiss", after)
            print(f"dismissed: {fmt(after)}", flush=True)
            break
        log("B_modal", v)

    # D/E: west door + Barry cutscene; sample DURING via plain frameadvance
    b.step({"left": True}, 300)
    b.step({"up": True}, 30)
    b.step({"cross": True}, 4)
    for i in range(150):
        b.frameadvance(10)
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        if not hi & 0x80:
            log("DE_door_scene", v)
            if i % 20 == 0:
                print(f"[scene] {fmt(v)}", flush=True)
        else:
            log("F_after_scene", v)
            if i > 30:
                break

    b.step({"up": True}, 16)
    v = read(b)
    log("F_after_scene", v)
    print(f"final: {fmt(v)}", flush=True)

    show()
    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
