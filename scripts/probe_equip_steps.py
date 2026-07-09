"""Step-by-step screenshot probe of equipping the beretta (slot 1)."""

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
OUT = ROOT / "data" / "equip_probe"


def _tap(client: BizHawkClient, buttons: dict[str, bool], *, frames: int) -> None:
    client.step(buttons=buttons, n=int(frames))


def shot(client: BizHawkClient, name: str) -> None:
    import cv2

    rgb = client.screenshot()
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    print(f"[probe] {name}", flush=True)


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

        _tap(client, {"start": True}, frames=12)
        _tap(client, {}, frames=40)
        shot(client, "s01_item_screen_open")

        _tap(client, {"right": True}, frames=8)
        _tap(client, {}, frames=10)
        shot(client, "s02_cursor_right_beretta")

        _tap(client, {"cross": True}, frames=15)
        _tap(client, {}, frames=15)
        shot(client, "s03_submenu_open")

        _tap(client, {"cross": True}, frames=15)
        _tap(client, {}, frames=15)
        shot(client, "s04_after_equip_press")

        _tap(client, {"start": True}, frames=12)
        _tap(client, {}, frames=30)
        shot(client, "s05_back_in_control")

        val = client.read_block(0x800C51A8, 8)
        print("[probe] 0x800C51A8..AF:", " ".join(f"{b:02X}" for b in val))
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
