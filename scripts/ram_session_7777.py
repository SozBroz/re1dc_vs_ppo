"""Start BizHawk on a bridge port, load training savestate, dump RAM, keep session alive."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.fresh_spawn import format_spawn_summary
from re1_rl.game_session import episode_failure_reason, outside_gameplay_reason
from re1_rl.ram_skip import (
    in_control_from_ram,
    item_inventory_screen_from_ram,
    needs_skip_from_ram,
)
from re1_rl.memory_map import (
    DEFAULT_RAM_FIELDS,
    INVENTORY_BASE,
    ITEM_BOX_BASE,
    ITEM_IDS,
    MESSAGE_FLAG,
    SCENE_FLAG,
    decode_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
DEFAULT_QUICKSAVE = (
    ROOT
    / "tools"
    / "BizHawk-2.11.1"
    / "PSX"
    / "State"
    / "Resident Evil - Director's Cut (USA).Nymashock.QuickSave7.State"
)


def decode_box(block: list[int], n: int = 15) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for i in range(n):
        iid, qty = block[i * 2], block[i * 2 + 1]
        if iid or qty:
            name = ITEM_IDS.get(iid, f"0x{iid:02X}")
            out.append((iid, qty, name))
    return out


def dump_ram(client: BizHawkClient, *, port: int) -> None:
    fields = list(DEFAULT_RAM_FIELDS) + [
        ("msg_flag", MESSAGE_FLAG, "u8"),
        ("scene_flag", SCENE_FLAG, "u8"),
    ]
    ram = client.read_ram(fields)
    inv = decode_inventory(ram)
    box_raw = client.read_block(ITEM_BOX_BASE, 32)
    room = f"{int(ram['stage_id']) + 1}{int(ram['room_id']):02X}"
    gs = int(ram["game_state"])
    mode = int(ram["game_mode"])
    outside = outside_gameplay_reason(ram, episode_start_hp=96)
    failure = episode_failure_reason(ram, episode_start_hp=96, prev_hp=96)
    print(f"\n=== RAM @ port {port} ===")
    print(format_spawn_summary(ram))
    print(
        f"room_code={room} gs=0x{gs:08X} mode=0x{mode:02X} "
        f"msg=0x{int(ram.get('msg_flag', 0)):02X} scene=0x{int(ram.get('scene_flag', 0)):02X}",
        flush=True,
    )
    print(f"outside={outside!r} episode_failure={failure!r}", flush=True)
    print(
        f"skip: in_control={in_control_from_ram(ram)} needs_skip={needs_skip_from_ram(ram)} "
        f"item_inventory={item_inventory_screen_from_ram(ram)}",
        flush=True,
    )
    print(f"pos=({ram['player_x']},{ram['player_y']},{ram['player_z']}) facing={ram['player_facing']}")
    print(f"inventory: {inv}")
    print(f"ITEM_BOX @ 0x{ITEM_BOX_BASE:08X}: {' '.join(f'{b:02X}' for b in box_raw)}")
    for iid, qty, name in decode_box(box_raw):
        print(f"  box: {name} x{qty} (0x{iid:02X})")
    print(f"INVENTORY @ 0x{INVENTORY_BASE:08X}: {client.read_block(INVENTORY_BASE, 16)}")
    print("=== end RAM ===\n", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7790)
    ap.add_argument("--poll-s", type=float, default=2.0)
    ap.add_argument("--no-load-state", action="store_true")
    ap.add_argument(
        "--savestate",
        default=None,
        help="savestate path (default: QuickSave7 if present, else jill_control_fresh)",
    )
    args = ap.parse_args()
    port = int(args.port)
    if args.savestate:
        state_path = Path(args.savestate)
        if not state_path.is_absolute():
            state_path = ROOT / state_path
    elif DEFAULT_QUICKSAVE.is_file():
        state_path = DEFAULT_QUICKSAVE
    else:
        state_path = STATE

    client = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    client.start_server()
    print(f"[{port}] listening — launching EmuHawk...", flush=True)
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        client.wait_for_client()
        print(f"[{port}] connected", flush=True)
        if not args.no_load_state:
            client.load_savestate(str(state_path.resolve()))
            print(f"[{port}] loaded {state_path}", flush=True)
            client.frameadvance(5)
        dump_ram(client, port=port)
        print(
            f"[{port}] session live — open START/ITEM (status+ECG); "
            f"RAM dumps every {args.poll_s}s; Ctrl+C to quit",
            flush=True,
        )
        frames_per_poll = max(1, int(60 * float(args.poll_s)))
        while True:
            client.frameadvance(frames_per_poll)
            dump_ram(client, port=port)
    except KeyboardInterrupt:
        print(f"[{port}] shutting down", flush=True)
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
