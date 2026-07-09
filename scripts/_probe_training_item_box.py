"""Read item box RAM from the training init savestate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import ITEM_BOX_BASE, ITEM_IDS, decode_inventory

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
PORT = 5566


def main() -> int:
    client = BizHawkClient(port=PORT, timeout=120.0, connect_timeout=120.0)
    client.start_server()
    print("listening, launching EmuHawk...", flush=True)
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={PORT}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client.wait_for_client()
    print("connected", flush=True)

    client.load_savestate(str(STATE.resolve()))
    client.frameadvance(5)
    ram = client.read_ram()
    inv = decode_inventory(ram)
    box = client.read_block(ITEM_BOX_BASE, 64)
    room = f"{int(ram['stage_id']) + 1}{int(ram['room_id']):02X}"

    print(f"state: {STATE.name} (training init)")
    print(f"hp={ram.get('player_hp')} room={room} char={ram.get('character_id')}")
    print(f"on-person: {inv}")
    print(f"ITEM_BOX_BASE 0x{ITEM_BOX_BASE:08X}:")
    print(f"  raw: {' '.join(f'{b:02X}' for b in box[:32])}")

    any_slot = False
    for i in range(15):
        iid, qty = box[i * 2], box[i * 2 + 1]
        if iid or qty:
            any_slot = True
            name = ITEM_IDS.get(iid, f"0x{iid:02X}")
            print(f"  slot {i}: id=0x{iid:02X} qty={qty} ({name})")
    if not any_slot:
        print("  (all slots empty — box region still readable at fixed address)")

    client.quit()
    proc.terminate()
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
