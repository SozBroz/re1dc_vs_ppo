"""Confirm equipped-weapon RAM: inject shotgun into inventory slot 2, equip it.

Expected if hypothesis holds:
  0x800C50BE (equipped inventory slot index) -> 0x02
  0x800C50A4 (equipped weapon type code)     -> new code (not 0x01/0x03)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import INVENTORY_BASE

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
OUT = ROOT / "data" / "equip_probe"

CANDIDATES = [0x800C50A3, 0x800C50A4, 0x800C50AE, 0x800C50AF, 0x800C50BE]


def _tap(client: BizHawkClient, buttons: dict[str, bool], *, frames: int) -> None:
    client.step(buttons=buttons, n=int(frames))


def shot(client: BizHawkClient, name: str) -> None:
    import cv2

    rgb = client.screenshot()
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    print(f"[probe] {name}", flush=True)


def read_candidates(client: BizHawkClient, label: str) -> None:
    vals = {a: client.read_block(a, 1)[0] for a in CANDIDATES}
    pretty = " ".join(f"0x{a:08X}=0x{v:02X}" for a, v in vals.items())
    print(f"[probe] {label}: {pretty}", flush=True)


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
        read_candidates(client, "fresh (knife equipped)")

        # Inject shotgun (0x03) qty 7 into inventory slot 2 (was empty).
        client.write_ram([("inv2", INVENTORY_BASE + 4, "u16", 0x0703)])
        client.frameadvance(5)
        print("[probe] shotgun written to inventory slot 2", flush=True)

        # Equip it: cursor homes on slot 0 fresh; moves: down -> slot 2.
        _tap(client, {"start": True}, frames=12)
        _tap(client, {}, frames=40)
        shot(client, "sg01_item_screen")
        _tap(client, {"down": True}, frames=8)
        _tap(client, {}, frames=10)
        shot(client, "sg02_cursor_slot2")
        _tap(client, {"cross": True}, frames=15)
        _tap(client, {}, frames=15)
        _tap(client, {"cross": True}, frames=15)  # EQUIP
        _tap(client, {}, frames=15)
        shot(client, "sg03_after_equip")
        _tap(client, {"start": True}, frames=12)
        _tap(client, {}, frames=30)
        shot(client, "sg04_in_control")

        read_candidates(client, "shotgun equipped")
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
