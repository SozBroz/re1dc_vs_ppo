"""Probe the door-skip / cutscene-turbo patch sites on the live game.

Reads the original code halfwords at the nolberto82 patch addresses
(SLUS-00551), installs the patches, and re-reads to confirm the writes land.

Run (server first, then EmuHawk with --socket_port matching --port):
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_door_patch.py --port 5702
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import (
    CUTSCENE_TURBO_ADDR,
    DOOR_SKIP_PATCH_ADDR,
)
from re1_rl.ram_skip import RamSkipper

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "states" / "jill_control.State"


def dump(bridge: BizHawkClient, label: str) -> None:
    for name, addr in (("door_skip", DOOR_SKIP_PATCH_ADDR), ("turbo", CUTSCENE_TURBO_ADDR)):
        # full instruction word (aligned) + the patched halfword
        word_addr = addr & ~0x3
        b = bridge.read_block(word_addr, 8)
        words = [
            b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24),
            b[4] | (b[5] << 8) | (b[6] << 16) | (b[7] << 24),
        ]
        half = bridge.read_ram([("h", addr, "u16")])["h"]
        print(
            f"{label} {name}: instr@0x{word_addr:08X}=0x{words[0]:08X} "
            f"next=0x{words[1]:08X} halfword@0x{addr:08X}=0x{half:04X}",
            flush=True,
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5702)
    args = ap.parse_args()

    bridge = BizHawkClient(port=args.port, timeout=300.0)
    bridge.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    bridge.wait_for_client()
    bridge.set_speed(6400)

    bridge.load_savestate(str(CONTROL))
    bridge.frameadvance(2)
    dump(bridge, "pre-patch ")

    RamSkipper(bridge).install_engine_patches()
    bridge.frameadvance(2)
    dump(bridge, "post-patch")

    bridge.set_speed(100)
    bridge.quit()
    bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
