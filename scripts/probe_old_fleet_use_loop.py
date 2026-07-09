"""Simulate old fleet: forced USE on full-HP spray in a loop; count emu frames + RAM."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.game_session import outside_gameplay_reason
from re1_rl.inventory_menu_macro import execute_use_macro
from re1_rl.memory_map import GAME_MODE, GAME_STATE, PLAYER_HP

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"


def snap(client: BizHawkClient) -> dict:
    ram = client.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
            ("player_hp", PLAYER_HP, "u16"),
        ]
    )
    mode = int(ram["game_mode"])
    return {
        "game_mode": f"0x{mode:02X}",
        "game_state": f"0x{int(ram['game_state']):08X}",
        "hp": int(ram["player_hp"]),
        "in_control": bool(mode & 0x80),
        "outside": outside_gameplay_reason(ram, episode_start_hp=96),
    }


def main() -> int:
    port = 7777
    client = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    client.start_server()
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    total = 0
    try:
        client.wait_for_client()
        client.load_savestate(str(STATE.resolve()))
        client.frameadvance(5)
        print(f"[old-fleet] reset {snap(client)}", flush=True)
        with patch("re1_rl.inventory_menu_macro.use_would_help", return_value=True):
            for i in range(12):
                died, frames, report = execute_use_macro(
                    client, 2, prev_hp=96, episode_start_hp=96,
                )
                total += frames
                post = snap(client)
                print(
                    f"[old-fleet] loop={i} macro_frames={frames} total={total} "
                    f"reason={report.get('reason')} ok={report.get('ok')} "
                    f"gm={post['game_mode']} ctrl={post['in_control']} "
                    f"outside={post['outside']!r}",
                    flush=True,
                )
                if not post["in_control"]:
                    print("[old-fleet] STUCK IN MENU", flush=True)
        print(f"[old-fleet] TOTAL_MACRO_FRAMES={total}", flush=True)
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
