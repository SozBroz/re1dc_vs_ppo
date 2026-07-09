"""Screenshot every phase of the pause-menu equip flow to debug navigation.

Saves PNGs to data/equip_probe/ and dumps a wide RAM diff after each phase.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
OUT = ROOT / "data" / "equip_probe"

WIDE_BASE = 0x800C3000
WIDE_COUNT = 0x6000  # 0x800C3000..0x800C9000


def _tap(client: BizHawkClient, buttons: dict[str, bool], *, frames: int) -> None:
    client.step(buttons=buttons, n=int(frames))


def shot(client: BizHawkClient, name: str) -> None:
    import cv2

    rgb = client.screenshot()
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    print(f"[probe] saved {name}.png", flush=True)


def snapshot(client: BizHawkClient) -> np.ndarray:
    return np.array(client.read_block(WIDE_BASE, WIDE_COUNT), dtype=np.uint8)


def diff(a: np.ndarray, b: np.ndarray, label: str, *, limit: int = 30) -> None:
    idx = np.nonzero(a != b)[0]
    print(f"[probe] {label}: {len(idx)} bytes changed", flush=True)
    for i in idx[:limit]:
        print(f"  0x{WIDE_BASE + int(i):08X}: 0x{int(a[i]):02X} -> 0x{int(b[i]):02X}")


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
        shot(client, "00_in_control")
        base = snapshot(client)

        _tap(client, {"start": True}, frames=12)
        _tap(client, {}, frames=40)
        shot(client, "01_after_start")

        _tap(client, {"down": True}, frames=10)
        _tap(client, {}, frames=10)
        shot(client, "02_after_down")

        _tap(client, {"cross": True}, frames=20)
        _tap(client, {}, frames=30)
        shot(client, "03_after_cross")

        _tap(client, {"cross": True}, frames=15)
        _tap(client, {}, frames=15)
        shot(client, "04_after_cross2")

        _tap(client, {"cross": True}, frames=15)
        _tap(client, {}, frames=15)
        shot(client, "05_after_cross3")

        _tap(client, {"triangle": True}, frames=15)
        _tap(client, {}, frames=20)
        shot(client, "06_after_triangle")

        _tap(client, {"triangle": True}, frames=15)
        _tap(client, {}, frames=20)
        shot(client, "07_after_triangle2")

        _tap(client, {"start": True}, frames=12)
        _tap(client, {}, frames=30)
        shot(client, "08_after_start_close")

        after = snapshot(client)
        diff(base, after, "in_control -> after menu round trip")
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
