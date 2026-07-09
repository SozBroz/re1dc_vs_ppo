"""Hunt the equipped-weapon RAM byte (SLUS-00551).

START opens the ITEM screen directly (probe 2026-07-07); cursor homes on
slot 0. Weapon submenu top entry is EQUIP. Flow per equip: START, move to
slot, cross (submenu), cross (EQUIP), START (close screen).

Cycle: equip beretta (slot 1) -> snapshot -> equip knife (slot 0) ->
snapshot -> equip beretta -> snapshot. Candidates must read
0x02 / 0x01 / 0x02. Prime suspect from the probe: 0x800C51AB.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"

REGIONS: list[tuple[int, int]] = [
    (0x800C3000, 0x1000),
    (0x800C5000, 0x4000),
]
OUT = ROOT / "data" / "equip_probe"

KNIFE_ID = 0x01
BERETTA_ID = 0x02


def _tap(client: BizHawkClient, buttons: dict[str, bool], *, frames: int) -> None:
    client.step(buttons=buttons, n=int(frames))


def equip_move(client: BizHawkClient, moves: list[str]) -> None:
    """Open ITEM screen, apply relative cursor moves, EQUIP, close.

    NOTE: pressing up from the grid's top row jumps to the MAP/FILE/EXIT
    header — never normalize with blind ups. The screen remembers the
    cursor slot between opens; callers pass relative moves.
    """
    _tap(client, {"start": True}, frames=12)
    _tap(client, {}, frames=40)
    for m in moves:
        _tap(client, {m: True}, frames=8)
        _tap(client, {}, frames=10)
    _tap(client, {"cross": True}, frames=15)  # open submenu
    _tap(client, {}, frames=15)
    _tap(client, {"cross": True}, frames=15)  # EQUIP (top entry for weapons)
    _tap(client, {}, frames=15)
    _tap(client, {"start": True}, frames=12)  # close ITEM screen
    _tap(client, {}, frames=30)


def shot(client: BizHawkClient, name: str) -> None:
    import cv2

    rgb = client.screenshot()
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def snapshot(client: BizHawkClient) -> dict[int, int]:
    out: dict[int, int] = {}
    for base, count in REGIONS:
        block = client.read_block(base, count)
        for i, b in enumerate(block):
            out[base + i] = int(b)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    args = ap.parse_args()
    port = int(args.port)

    client = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
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

        # fresh open: cursor on slot 0 (knife); right = beretta
        equip_move(client, ["right"])  # beretta
        shot(client, "hunt_10_beretta_equipped")
        snap_b1 = snapshot(client)
        print("[hunt] snapshot 1 (beretta) taken", flush=True)

        equip_move(client, ["left"])  # knife (cursor remembered on beretta)
        shot(client, "hunt_11_knife_equipped")
        snap_k = snapshot(client)
        print("[hunt] snapshot 2 (knife) taken", flush=True)

        equip_move(client, ["right"])  # beretta again
        shot(client, "hunt_12_beretta_again")
        snap_b2 = snapshot(client)
        print("[hunt] snapshot 3 (beretta) taken", flush=True)

        exact: list[int] = []
        toggled: list[int] = []
        for addr in snap_b1:
            vb1, vk, vb2 = snap_b1[addr], snap_k[addr], snap_b2[addr]
            if vb1 == BERETTA_ID and vk == KNIFE_ID and vb2 == BERETTA_ID:
                exact.append(addr)
            elif vb1 == vb2 and vb1 != vk:
                toggled.append(addr)

        print(f"\n[hunt] EXACT candidates (0x02/0x01/0x02): {len(exact)}")
        for addr in exact:
            print(
                f"  0x{addr:08X}: beretta=0x{snap_b1[addr]:02X} "
                f"knife=0x{snap_k[addr]:02X} beretta2=0x{snap_b2[addr]:02X}"
            )
        print(f"[hunt] toggled candidates (b==b2 != k): {len(toggled)}")
        for addr in toggled[:40]:
            print(
                f"  0x{addr:08X}: beretta=0x{snap_b1[addr]:02X} "
                f"knife=0x{snap_k[addr]:02X} beretta2=0x{snap_b2[addr]:02X}"
            )
        prime = 0x800C51AB
        print(
            f"[hunt] prime suspect 0x{prime:08X}: "
            f"beretta=0x{snap_b1[prime]:02X} knife=0x{snap_k[prime]:02X} "
            f"beretta2=0x{snap_b2[prime]:02X}"
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
