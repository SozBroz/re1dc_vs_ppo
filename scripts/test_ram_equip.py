"""Test RAM-editing the equipped weapon (no menus).

Fresh state: knife equipped in inventory slot 0. Write slot 0 = (weapon_id,
qty) and both equipped-id mirrors, then hold R1 and press cross; record anim
hooks and ammo qty. If ammo decrements / anim leaves knife track, the engine
honors RAM equips and the per-weapon frame-data harness can skip menus.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import INVENTORY_BASE, ITEM_IDS
from re1_rl.knife_macro import read_knife_hooks

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"

EQUIPPED_ID_PLAYER = 0x800C5126
EQUIPPED_ID_SAVE = 0x800C8689

TEST_WEAPONS = [
    (0x02, 15, "beretta"),
    (0x03, 7, "shotgun"),
    (0x05, 6, "colt_python"),
    (0x01, 0, "knife"),
]


def ram_equip(client: BizHawkClient, weapon_id: int, qty: int) -> None:
    client.write_ram([
        ("inv0", INVENTORY_BASE, "u16", (qty << 8) | weapon_id),
        ("eq_player", EQUIPPED_ID_PLAYER, "u8", weapon_id),
        ("eq_save", EQUIPPED_ID_SAVE, "u8", weapon_id),
    ])
    client.frameadvance(5)


def slot0(client: BizHawkClient) -> tuple[int, int]:
    raw = int(client.read_ram([("inv0", INVENTORY_BASE, "u16")])["inv0"])
    return raw & 0xFF, raw >> 8


def fire_probe(client: BizHawkClient, name: str) -> None:
    """Hold R1 to aim, tap cross, sample hooks + ammo."""
    pre_id, pre_qty = slot0(client)
    trail: list[str] = []
    for f in range(30):
        client.step(buttons={"r1": True}, n=2)
        anim, aux, rec = read_knife_hooks(client)
        if f % 5 == 0:
            trail.append(f"aim_f{f * 2}:anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}")
    for f in range(10):
        client.step(buttons={"r1": True, "cross": True}, n=2)
        anim, aux, rec = read_knife_hooks(client)
        if f % 2 == 0:
            trail.append(f"fire_f{f * 2}:anim=0x{anim:02X} aux=0x{aux:02X} rec={rec}")
    for f in range(20):
        client.step(buttons={"r1": True}, n=2)
    client.step(buttons={}, n=20)
    post_id, post_qty = slot0(client)
    print(f"[fire] {name}: slot0 {ITEM_IDS.get(pre_id, hex(pre_id))} "
          f"qty {pre_qty} -> {post_qty}", flush=True)
    print(f"  trail: {' | '.join(trail)}", flush=True)


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
        for weapon_id, qty, name in TEST_WEAPONS:
            client.load_savestate(str(STATE.resolve()))
            client.frameadvance(10)
            ram_equip(client, weapon_id, qty)
            print(f"\n[test] RAM-equipped {name} (0x{weapon_id:02X}) qty={qty}",
                  flush=True)
            fire_probe(client, name)
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
