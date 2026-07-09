"""One-off: compare two BizHawk QuickSaves for item-box RAM layout."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import (
    INVENTORY_BASE,
    ITEM_BOX_BASE,
    ITEM_IDS,
    decode_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
PORT = 5566


def decode_slots(block: list[int], n: int = 15) -> list[tuple[int, int, int, str]]:
    out: list[tuple[int, int, int, str]] = []
    for i in range(n):
        item_id = block[i * 2]
        qty = block[i * 2 + 1]
        if item_id or qty:
            name = ITEM_IDS.get(item_id, f"unknown_0x{item_id:02X}")
            out.append((i, item_id, qty, name))
    return out


def summarize(client: BizHawkClient, label: str, path: Path) -> tuple[list[int], list[int], dict]:
    client.load_savestate(str(path.resolve()))
    client.frameadvance(5)
    ram = client.read_ram()
    inv = decode_inventory(ram)
    box_raw = client.read_block(ITEM_BOX_BASE, 64)
    inv_raw = client.read_block(INVENTORY_BASE, 32)
    room = int(ram.get("room_id", -1))
    stage = int(ram.get("stage_id", -1))
    room_code = f"{stage + 1}{room:02X}"
    print(f"\n=== {label} ===")
    print(f"file: {path.name}")
    print(
        f"hp={ram.get('player_hp')} room={room_code} "
        f"mode=0x{int(ram.get('game_mode', 0)):02X} "
        f"gs=0x{int(ram.get('game_state', 0)):08X}"
    )
    print(f"on-person inventory: {inv}")
    print(f"item box slots (ITEM_BOX_BASE 0x{ITEM_BOX_BASE:08X}):")
    for i, iid, qty, name in decode_slots(box_raw, 15):
        print(f"  slot {i}: id=0x{iid:02X} qty={qty} ({name})")
    print(f"raw box hex: {' '.join(f'{b:02X}' for b in box_raw[:30])}")
    return box_raw, inv_raw, ram


def main() -> int:
    states = sorted(STATE_DIR.glob("*.QuickSave*.State"), key=lambda p: p.stat().st_mtime)
    if len(states) < 2:
        print("need at least 2 QuickSave states", file=sys.stderr)
        return 1
    s5, s6 = states[-2], states[-1]
    print(f"comparing:\n  A (older): {s5.name}\n  B (newer): {s6.name}")

    client = BizHawkClient(port=PORT, timeout=180.0, connect_timeout=180.0)
    client.start_server()
    print(f"listening on 127.0.0.1:{PORT} — launching EmuHawk...", flush=True)
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client.wait_for_client()
    print("connected", flush=True)

    b5, i5, _ = summarize(client, "QuickSave5 (save room, default box)", s5)
    b6, i6, _ = summarize(client, "QuickSave6 (knife deposited)", s6)

    print("\n=== BYTE DIFF item box region (64 bytes @ ITEM_BOX_BASE) ===")
    for off in range(64):
        if b5[off] != b6[off]:
            addr = ITEM_BOX_BASE + off
            print(f"  +0x{off:02X} bus=0x{addr:08X}: {b5[off]:02X} -> {b6[off]:02X}")

    print("\n=== BYTE DIFF on-person inventory (16 bytes @ INVENTORY_BASE) ===")
    for off in range(16):
        if i5[off] != i6[off]:
            addr = INVENTORY_BASE + off
            print(f"  +0x{off:02X} bus=0x{addr:08X}: {i5[off]:02X} -> {i6[off]:02X}")

    lo, hi = 0x800C8700, 0x800C8800
    client.load_savestate(str(s5.resolve()))
    client.frameadvance(3)
    blk5 = client.read_block(lo, hi - lo)
    client.load_savestate(str(s6.resolve()))
    client.frameadvance(3)
    blk6 = client.read_block(lo, hi - lo)
    print(f"\n=== ALL DIFFS in 0x{lo:08X}-0x{hi:08X} ({hi - lo} bytes) ===")
    for off in range(hi - lo):
        if blk5[off] != blk6[off]:
            print(f"  bus=0x{lo + off:08X} (+0x{off:03X}): {blk5[off]:02X} -> {blk6[off]:02X}")

    client.quit()
    proc.terminate()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
