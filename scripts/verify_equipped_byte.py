"""Verify equipped-weapon byte candidates 0x800C5126 / 0x800C8689.

Equips knife -> beretta -> shotgun (injected) with in-control gating and
screenshots; reads candidates only while game_state == in-control.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import GAME_STATE, INVENTORY_BASE

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
OUT = ROOT / "data" / "equip_probe"

CAND_A = 0x800C5126
CAND_B = 0x800C8689
IN_CONTROL_GS = 0x80800004


def _tap(client: BizHawkClient, buttons: dict[str, bool], *, frames: int) -> None:
    client.step(buttons=buttons, n=int(frames))


def shot(client: BizHawkClient, name: str) -> None:
    import cv2

    rgb = client.screenshot()
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def game_state(client: BizHawkClient) -> int:
    return int(client.read_ram([("gs", GAME_STATE, "u32")])["gs"])


def wait_in_control(client: BizHawkClient, *, max_frames: int = 300) -> bool:
    waited = 0
    while waited < max_frames:
        if game_state(client) == IN_CONTROL_GS:
            return True
        client.frameadvance(10)
        waited += 10
    return False


def read_candidates(client: BizHawkClient, label: str) -> tuple[int, int]:
    a = client.read_block(CAND_A, 1)[0]
    b = client.read_block(CAND_B, 1)[0]
    gs = game_state(client)
    print(
        f"[verify] {label}: 0x{CAND_A:08X}=0x{a:02X} 0x{CAND_B:08X}=0x{b:02X} "
        f"gs=0x{gs:08X}",
        flush=True,
    )
    return a, b


def equip(client: BizHawkClient, moves: list[str], name: str) -> None:
    _tap(client, {"start": True}, frames=12)
    _tap(client, {}, frames=40)
    for m in moves:
        _tap(client, {m: True}, frames=8)
        _tap(client, {}, frames=10)
    _tap(client, {"cross": True}, frames=15)
    _tap(client, {}, frames=15)
    _tap(client, {"cross": True}, frames=15)  # EQUIP
    _tap(client, {}, frames=20)
    shot(client, f"v_{name}_before_close")
    _tap(client, {"start": True}, frames=12)
    _tap(client, {}, frames=30)
    if not wait_in_control(client):
        # maybe still in menu — tap start once more
        _tap(client, {"start": True}, frames=12)
        _tap(client, {}, frames=30)
        wait_in_control(client)
    shot(client, f"v_{name}_final")


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
        client.write_ram([("inv2", INVENTORY_BASE + 4, "u16", 0x0703)])
        client.frameadvance(5)

        read_candidates(client, "knife (fresh state)")

        equip(client, ["right"], "beretta")
        read_candidates(client, "beretta equipped")

        equip(client, ["left", "down"], "shotgun")
        read_candidates(client, "shotgun equipped")

        equip(client, ["up"], "knife_again")
        read_candidates(client, "knife re-equipped")
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
