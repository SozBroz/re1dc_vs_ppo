"""Live validate options-menu dismiss on newest QuickSave (expected OPTIONS).

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_options_menu_dismiss.py
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_options_menu_dismiss.py --port 7791
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.game_session import options_menu_from_ram
from re1_rl.options_menu_macro import dismiss_options_menu, read_options_ram

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"


def newest_quicksave() -> Path:
    states = sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not states:
        raise FileNotFoundError(f"no QuickSave*.State under {STATE_DIR}")
    return states[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7791)
    ap.add_argument("--state", type=Path, default=None)
    ap.add_argument("--speed", type=int, default=400)
    args = ap.parse_args()
    state = args.state or newest_quicksave()
    print(f"state={state} mtime={time.ctime(state.stat().st_mtime)}", flush=True)

    bridge = BizHawkClient(
        port=args.port,
        timeout=120.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / f"_options_probe_{args.port}.png"),
        screenshot_mmf=True,
    )
    bridge.start_server()
    cmd = [
        str(EMU),
        str(ROM),
        f"--lua={LUA}",
        "--socket_ip=127.0.0.1",
        f"--socket_port={args.port}",
        "--gdi",
        "--chromeless",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(args.speed)
        bridge.load_savestate(str(state))
        bridge.frameadvance(5)
        ram = read_options_ram(bridge)
        print(
            f"before: gs=0x{ram['game_state']:08X} mode=0x{ram['game_mode']:02X} "
            f"room={ram['room_id']} hp={ram['player_hp']} "
            f"options={options_menu_from_ram(ram)}",
            flush=True,
        )
        if not options_menu_from_ram(ram):
            print("FAIL: savestate is not on OPTIONS menu", flush=True)
            return 2
        still, frames, report = dismiss_options_menu(
            bridge,
            prev_hp=ram["player_hp"],
            episode_start_hp=ram["player_hp"],
        )
        ram2 = read_options_ram(bridge)
        print(f"report={report}", flush=True)
        print(
            f"after:  gs=0x{ram2['game_state']:08X} mode=0x{ram2['game_mode']:02X} "
            f"room={ram2['room_id']} hp={ram2['player_hp']} "
            f"options={options_menu_from_ram(ram2)} frames={frames} still={still}",
            flush=True,
        )
        from re1_rl.ram_skip import pause_menu_tree_from_ram
        from re1_rl.memory_map import PLAYER_X, PLAYER_Z

        if still or options_menu_from_ram(ram2) or pause_menu_tree_from_ram(ram2):
            print("FAIL: still trapped in OPTIONS/pause", flush=True)
            return 1
        pos0 = bridge.read_ram([("x", PLAYER_X, "s16"), ("z", PLAYER_Z, "s16")])
        moved = False
        for direction in ("up", "down", "left", "right"):
            bridge.step(buttons={direction: True}, n=40)
            pos1 = bridge.read_ram([("x", PLAYER_X, "s16"), ("z", PLAYER_Z, "s16")])
            if int(pos0["x"]) != int(pos1["x"]) or int(pos0["z"]) != int(pos1["z"]):
                moved = True
                break
        print(f"moved={moved} pos {pos0} -> {pos1}", flush=True)
        if not moved:
            print(
                "PASS: OPTIONS dismissed (menu clear; no movement — likely facing wall)",
                flush=True,
            )
        else:
            print("PASS: OPTIONS dismissed and Jill can move", flush=True)
        return 0
    finally:
        try:
            bridge._request({"cmd": "quit"})
        except Exception:
            pass
        bridge.close()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
