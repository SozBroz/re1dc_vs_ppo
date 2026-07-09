"""Diagnostic: what does GAME_STATE do during dialogue scenes (Kenneth path)?

Teleports Jill (RAM position write) to the tea-room door (105->104), enters,
and logs the raw GAME_STATE dword through the Kenneth discovery scene.
Movement probes give ground truth about control.

Goal: find the RAM signature that distinguishes "dialogue playing" from
"player control" if bit 0x80 of the high byte stays set during the scene.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_cutscene_modes.py --port 5712
  (then launch EmuHawk with --socket_port=5712)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import (
    GAME_STATE,
    PLAYER_FACING,
    PLAYER_X,
    PLAYER_Z,
    ROOM_ID,
    STAGE_ID,
)

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "states" / "jill_control_fresh.State"

POLL = [
    ("state", GAME_STATE, "u32"),
    ("stage", STAGE_ID, "u8"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
    ("facing", PLAYER_FACING, "u16"),
]

# 105 -> 104 (tea room / Kenneth) door, from doors_empirical.json
DOOR_X, DOOR_Z = 7048, 14064
DOOR_FACING = 2320  # door_facing logged by harvest_doors.py


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def fmt(v: dict) -> str:
    hi = (v["state"] >> 24) & 0xFF
    return (f"state=0x{v['state']:08X} hi=0x{hi:02X} bit80={'1' if hi & 0x80 else '0'} "
            f"room={v['stage']}{v['room']:02X} pos=({v['x']},{v['z']}) f={v['facing']}")


def teleport(b: BizHawkClient, x: int, z: int, facing: int) -> None:
    b.write_ram([
        ("x", PLAYER_X, "u32", x),
        ("z", PLAYER_Z, "u32", z),
        ("facing", PLAYER_FACING, "u16", facing),
    ])
    b.frameadvance(4)


def observe(b: BizHawkClient, label: str, iters: int, stride: int = 10) -> dict:
    last = None
    v = read(b)
    for i in range(iters):
        b.frameadvance(stride)
        v = read(b)
        key = (v["state"], v["room"])
        if key != last:
            print(f"[{label}] f+{i*stride:5d} {fmt(v)}", flush=True)
            last = key
    return v


def probe_moves(b: BizHawkClient) -> bool:
    before = read(b)
    b.step({"up": True}, 12)
    after = read(b)
    return abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) > 16


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5712)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    b.load_savestate(str(FRESH))
    b.frameadvance(2)
    print(f"start: {fmt(read(b))}", flush=True)

    teleport(b, DOOR_X, DOOR_Z, DOOR_FACING)
    print(f"teleported: {fmt(read(b))}", flush=True)

    # press action at the door with the harvested facing; if the room does not
    # change, sweep facings (door may need exact approach vector)
    entered = False
    for facing in (DOOR_FACING, 0, 1024, 2048, 3072):
        teleport(b, DOOR_X, DOOR_Z, facing)
        b.step({"cross": True}, 4)
        b.frameadvance(30)
        v = read(b)
        if v["room"] != 5 or not ((v["state"] >> 24) & 0x80):
            print(f"door engaged with facing={facing}: {fmt(v)}", flush=True)
            entered = True
            break
        # also try walking forward into the trigger zone
        b.step({"up": True}, 20)
        v = read(b)
        if v["room"] != 5 or not ((v["state"] >> 24) & 0x80):
            print(f"door engaged by walk with facing={facing}: {fmt(v)}", flush=True)
            entered = True
            break
    if not entered:
        print(f"FAILED to enter 104: {fmt(read(b))}", flush=True)
        b.quit()
        b.close()
        print("DIAG_DONE", flush=True)
        return 1

    # watch the transition + Kenneth scene with no input
    v = observe(b, "kenneth", iters=400, stride=10)

    # ground truth + tap-through if scene stalls waiting for input
    for tap in range(20):
        moved = probe_moves(b)
        v = read(b)
        print(f"[probe {tap:2d}] moves={moved} {fmt(v)}", flush=True)
        if moved:
            break
        b.step({"cross": True}, 2)
        b.frameadvance(30)

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
