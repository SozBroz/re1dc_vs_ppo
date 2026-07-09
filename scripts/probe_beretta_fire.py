"""Menu-equip beretta, fire twice, find the ammo counter via wide RAM diff.

Also compares against the RAM-equip path to tell whether RAM equips
produce real shots.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.knife_macro import read_knife_hooks
from re1_rl.memory_map import INVENTORY_BASE

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
OUT = ROOT / "data" / "equip_probe"

BASE = 0x800C0000
COUNT = 0x10000
EQUIPPED_ID_PLAYER = 0x800C5126
EQUIPPED_ID_SAVE = 0x800C8689


def _tap(client: BizHawkClient, buttons: dict[str, bool], *, frames: int) -> None:
    client.step(buttons=buttons, n=int(frames))


def shot_png(client: BizHawkClient, name: str) -> None:
    import cv2

    rgb = client.screenshot()
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def menu_equip_beretta(client: BizHawkClient) -> None:
    _tap(client, {"start": True}, frames=12)
    _tap(client, {}, frames=40)
    _tap(client, {"right": True}, frames=8)
    _tap(client, {}, frames=10)
    _tap(client, {"cross": True}, frames=15)
    _tap(client, {}, frames=15)
    _tap(client, {"cross": True}, frames=15)  # EQUIP
    _tap(client, {}, frames=15)
    _tap(client, {"start": True}, frames=12)
    _tap(client, {}, frames=40)


def snap(client: BizHawkClient) -> np.ndarray:
    return np.array(client.read_block(BASE, COUNT), dtype=np.uint8)


def inv(client: BizHawkClient) -> list[int]:
    return client.read_block(INVENTORY_BASE, 16)


def fire_once(client: BizHawkClient, tag: str) -> None:
    for _ in range(30):
        client.step(buttons={"r1": True}, n=2)
    anim, aux, rec = read_knife_hooks(client)
    print(f"[{tag}] pre-fire hooks: anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}",
          flush=True)
    client.step(buttons={"r1": True, "cross": True}, n=4)
    shot_png(client, f"{tag}_muzzle")
    client.step(buttons={"r1": True}, n=30)
    client.step(buttons={}, n=20)


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

        # --- ground truth: menu equip ---
        client.load_savestate(str(STATE.resolve()))
        client.frameadvance(10)
        menu_equip_beretta(client)
        print("[menu] beretta equipped via menu", flush=True)
        print("[menu] inv before:", inv(client), flush=True)
        before = snap(client)
        fire_once(client, "menu_fire1")
        after = snap(client)
        print("[menu] inv after:", inv(client), flush=True)
        idx = np.nonzero(before != after)[0]
        print(f"[menu] bytes changed by one shot: {len(idx)}", flush=True)
        for i in idx[:50]:
            print(f"  0x{BASE + int(i):08X}: 0x{int(before[i]):02X} -> "
                  f"0x{int(after[i]):02X}")

        # --- RAM equip comparison ---
        client.load_savestate(str(STATE.resolve()))
        client.frameadvance(10)
        client.write_ram([
            ("inv0", INVENTORY_BASE, "u16", (15 << 8) | 0x02),
            ("eq_player", EQUIPPED_ID_PLAYER, "u8", 0x02),
            ("eq_save", EQUIPPED_ID_SAVE, "u8", 0x02),
        ])
        client.frameadvance(5)
        print("\n[ram] beretta RAM-equipped", flush=True)
        print("[ram] inv before:", inv(client), flush=True)
        before = snap(client)
        fire_once(client, "ram_fire1")
        after = snap(client)
        print("[ram] inv after:", inv(client), flush=True)
        idx = np.nonzero(before != after)[0]
        print(f"[ram] bytes changed by one shot: {len(idx)}", flush=True)
        for i in idx[:50]:
            print(f"  0x{BASE + int(i):08X}: 0x{int(before[i]):02X} -> "
                  f"0x{int(after[i]):02X}")
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
