"""Validate scene bit 0x10 of u8@0x800C3002 across all play phases.

Proposed skip condition:
  skip iff (u8@0x800C3003 & 0x80) == 0        (door/cutscene)  OR
           (u8@0x800C8665 & 0x80) != 0        (message box)    OR
           (u8@0x800C3002 & 0x10) != 0        (scripted scene, control frozen)

Phases checked (expect scene bit): idle(0) walk(0) aim(0) modal(?) door(?)
barry-frozen(1) post-scene control(0). Prints value everywhere; FAILs only if
idle/walk/aim/post-control have it set, or the frozen span doesn't.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_flag11.py --port 5727
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

SCENE_BYTE = 0x800C3002

POLL = [
    ("state", GAME_STATE, "u32"),
    ("scene", SCENE_BYTE, "u8"),
    ("msg", MESSAGE_FLAG, "u8"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]

failures: list[str] = []


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def fmt(v: dict) -> str:
    hi = (v["state"] >> 24) & 0xFF
    return (f"bit80={'1' if hi & 0x80 else '0'} scene=0x{v['scene']:02X} "
            f"msg={'1' if v['msg'] & 0x80 else '0'} room={v['room']} "
            f"pos=({v['x']},{v['z']})")


def check(phase: str, v: dict, want_scene_bit: bool | None) -> None:
    got = bool(v["scene"] & 0x10)
    if want_scene_bit is None:
        print(f"[  ? ] {phase}: {fmt(v)}", flush=True)
        return
    ok = got == want_scene_bit
    print(f"[{'OK ' if ok else 'FAIL'}] {phase}: {fmt(v)}", flush=True)
    if not ok:
        failures.append(phase)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5727)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)

    check("idle", read(b), False)
    b.step({"up": True}, 16)
    check("walk", read(b), False)
    b.step({"r1": True}, 16)
    check("aim", read(b), False)
    b.step({}, 4)

    # west door -> Barry scene; classify every 4-frame sample
    # (exact walk from _diag_dialog_flag10, which reliably reaches the door)
    b.step({"left": True}, 300)
    b.step({"up": True}, 30)
    b.step({"cross": True}, 4)

    seen_frozen = False
    seen_door = False
    f = 0
    while f < 6000:
        v = read(b)
        hi = (v["state"] >> 24) & 0xFF
        b80 = bool(hi & 0x80)
        msg = bool(v["msg"] & 0x80)
        scene = bool(v["scene"] & 0x10)
        if not b80:
            if not seen_door:
                check("door/cutscene span", v, None)
                seen_door = True
            b.frameadvance(4)
            f += 4
            continue
        if msg:
            b.step({"cross": True}, 2)
            b.frameadvance(2)
            f += 4
            continue
        if scene:
            if not seen_frozen:
                check("barry frozen span (scene bit)", v, True)
                seen_frozen = True
            b.frameadvance(4)
            f += 4
            continue
        # flags all say control: ground truth
        before = v
        b.step({"down": True}, 10)
        after = read(b)
        f += 10
        moved = abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) >= 8
        if moved:
            check("post-scene control", after, False)
            break
        print(f"[FAIL] frozen but scene bit CLEAR: {fmt(after)}", flush=True)
        failures.append("frozen w/o scene bit")
        b.frameadvance(20)
        f += 20

    if not seen_frozen:
        print("[warn] no frozen span sampled this run", flush=True)

    print(f"\nRESULT: {'ALL OK' if not failures else 'FAILURES: ' + ', '.join(failures)}",
          flush=True)
    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
