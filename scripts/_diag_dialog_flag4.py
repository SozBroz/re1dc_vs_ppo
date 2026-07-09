"""Phase-test modal discriminator candidates 0x800C300F / 0x800C3010.

From the control->modal RAM diff (Barry blood-check message box, bit80 SET):
  0x800C300F: 0x00 -> 0x40
  0x800C3010: 0x01 -> 0x00
Sample both across: control walk, modal open, modal dismissed, door/cutscene,
post-scene control, and (crucially) STAIRS/climb if touched. A usable flag
must be stable in control and distinct in the modal.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag4.py --port 5718
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

CAND_F = 0x800C300F
CAND_10 = 0x800C3010

POLL = [
    ("state", GAME_STATE, "u32"),
    ("f0f", CAND_F, "u8"),
    ("f10", CAND_10, "u8"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]

samples: dict[str, Counter] = {}


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def fmt(v: dict) -> str:
    hi = (v["state"] >> 24) & 0xFF
    return (f"bit80={'1' if hi & 0x80 else '0'} f0f=0x{v['f0f']:02X} "
            f"f10=0x{v['f10']:02X} room={v['room']} pos=({v['x']},{v['z']})")


def log(phase: str, v: dict) -> None:
    hi = (v["state"] >> 24) & 0xFF
    samples.setdefault(phase, Counter())[(hi & 0x80 != 0, v["f0f"], v["f10"])] += 1


def show() -> None:
    print("\n=== summary (bit80, f0f, f10) -> count ===", flush=True)
    for phase, ctr in samples.items():
        line = ", ".join(
            f"(bit80={'1' if k[0] else '0'},f0f=0x{k[1]:02X},f10=0x{k[2]:02X})x{n}"
            for k, n in sorted(ctr.items(), key=lambda kv: -kv[1]))
        print(f"  {phase}: {line}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5718)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)
    print(f"start: {fmt(read(b))}", flush=True)

    # A: control walk, sampling every step
    for mv in ("up", "left", "up", "right", "down", "up"):
        b.step({mv: True}, 16)
        log("A_control", read(b))

    # B: hunt the modal
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

    # C: dismiss
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

    show()
    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
