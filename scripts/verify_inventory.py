"""Verify the PS1 inventory block address predicted from the autosplitter.

Prediction: GOG inventory 0x7E9944 (0x838814 - EnglishGOG offset 0x4EED0)
mapped with the confirmed linear delta -0x7211C0 -> PS1 0x800C8784.
Layout per ASL: 2 bytes per slot (item_id, qty), 8 slots for Jill.

Loads states/jill_control.State (dining room, fresh start: expect beretta +
clip ammo in slots) and dumps the block plus 0x40 bytes either side so we can
eyeball the real base if the prediction is off by a few slots.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import ITEM_IDS

PREDICTED = 0x800C8784
WINDOW = 0x40


def main() -> int:
    bridge = BizHawkClient(timeout=300.0)
    bridge.start_server()
    print("listening; launch EmuHawk now", flush=True)
    bridge.wait_for_client()
    print("connected", flush=True)

    bridge.load_savestate(str(PROJECT_ROOT / "states" / "jill_control.State"))
    bridge.frameadvance(2)

    start = PREDICTED - WINDOW
    blk = bridge.read_block(start, 2 * WINDOW + 16)
    print(f"dump around 0x{PREDICTED:08X}:")
    for off in range(0, len(blk), 16):
        addr = start + off
        hexes = " ".join(f"{b:02X}" for b in blk[off:off + 16])
        mark = "  <-- predicted base" if addr <= PREDICTED < addr + 16 else ""
        print(f"  0x{addr:08X}: {hexes}{mark}")

    print("\npredicted slots (item_id, qty):")
    base_off = PREDICTED - start
    for slot in range(8):
        iid = blk[base_off + slot * 2]
        qty = blk[base_off + slot * 2 + 1]
        name = ITEM_IDS.get(iid, f"unknown_0x{iid:02X}") if iid else "-"
        print(f"  slot {slot}: id=0x{iid:02X} qty={qty}  {name}")

    bridge.quit()
    bridge.close()
    print("VERIFY_INVENTORY_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
