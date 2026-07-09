"""Wide differential hunt for the equipped-weapon byte.

Three states: knife (fresh), beretta, shotgun (RAM-injected into slot 2).
For each state take N spaced snapshots of 0x800C0000..0x800D0000; keep bytes
stable within every state but distinct across all three states.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import INVENTORY_BASE

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"

BASE = 0x800C0000
COUNT = 0x10000
SNAPS_PER_STATE = 3
GAP_FRAMES = 20


def _tap(client: BizHawkClient, buttons: dict[str, bool], *, frames: int) -> None:
    client.step(buttons=buttons, n=int(frames))


def equip_current_cursor_after_moves(client: BizHawkClient, moves: list[str]) -> None:
    _tap(client, {"start": True}, frames=12)
    _tap(client, {}, frames=40)
    for m in moves:
        _tap(client, {m: True}, frames=8)
        _tap(client, {}, frames=10)
    _tap(client, {"cross": True}, frames=15)
    _tap(client, {}, frames=15)
    _tap(client, {"cross": True}, frames=15)  # EQUIP
    _tap(client, {}, frames=15)
    _tap(client, {"start": True}, frames=12)
    _tap(client, {}, frames=30)


def stable_snapshot(client: BizHawkClient) -> tuple[np.ndarray, np.ndarray]:
    """(values, stable_mask) across SNAPS_PER_STATE spaced reads."""
    snaps = []
    for _ in range(SNAPS_PER_STATE):
        snaps.append(np.array(client.read_block(BASE, COUNT), dtype=np.uint8))
        client.frameadvance(GAP_FRAMES)
    ref = snaps[0]
    stable = np.ones(COUNT, dtype=bool)
    for s in snaps[1:]:
        stable &= s == ref
    return ref, stable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    args = ap.parse_args()
    port = int(args.port)

    client = BizHawkClient(port=port, timeout=600.0, connect_timeout=120.0)
    client.start_server()
    print(f"[{port}] listening — launching EmuHawk...", flush=True)
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1",
         f"--socket_port={port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        client.wait_for_client()
        print(f"[{port}] connected", flush=True)
        client.load_savestate(str(STATE.resolve()))
        client.frameadvance(10)
        # shotgun into empty slot 2 up front so inventory bytes stay constant
        client.write_ram([("inv2", INVENTORY_BASE + 4, "u16", 0x0703)])
        client.frameadvance(5)

        knife_vals, knife_stable = stable_snapshot(client)
        print("[hunt] knife state snapped", flush=True)

        equip_current_cursor_after_moves(client, ["right"])  # beretta slot 1
        beretta_vals, beretta_stable = stable_snapshot(client)
        print("[hunt] beretta state snapped", flush=True)

        # cursor remembered on beretta: left+down -> slot 2 (shotgun)
        equip_current_cursor_after_moves(client, ["left", "down"])
        shotgun_vals, shotgun_stable = stable_snapshot(client)
        print("[hunt] shotgun state snapped", flush=True)

        stable = knife_stable & beretta_stable & shotgun_stable
        distinct = (
            (knife_vals != beretta_vals)
            & (beretta_vals != shotgun_vals)
            & (knife_vals != shotgun_vals)
        )
        pairwise = (
            (knife_vals != beretta_vals)
            | (beretta_vals != shotgun_vals)
        )
        tri = np.nonzero(stable & distinct)[0]
        print(f"\n[hunt] bytes with 3 distinct stable values: {len(tri)}")
        for i in tri[:60]:
            print(
                f"  0x{BASE + int(i):08X}: knife=0x{int(knife_vals[i]):02X} "
                f"beretta=0x{int(beretta_vals[i]):02X} "
                f"shotgun=0x{int(shotgun_vals[i]):02X}"
            )
        duo = np.nonzero(stable & pairwise & ~distinct)[0]
        print(f"[hunt] bytes stable with 2 distinct values: {len(duo)} (first 40)")
        for i in duo[:40]:
            print(
                f"  0x{BASE + int(i):08X}: knife=0x{int(knife_vals[i]):02X} "
                f"beretta=0x{int(beretta_vals[i]):02X} "
                f"shotgun=0x{int(shotgun_vals[i]):02X}"
            )
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
