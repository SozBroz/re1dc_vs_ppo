"""Drive USE macro on full-HP spray; log RAM every button batch (repro menu stuck)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.game_session import outside_gameplay_reason
from re1_rl.inventory_menu_macro import (
    _equip_weapon_submenu,
    _navigate_slot,
    _pick_submenu_entry,
    close_item_screen,
    open_item_screen,
)
from re1_rl.memory_map import GAME_MODE, GAME_STATE, PLAYER_HP

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = ROOT / "states" / "jill_control_fresh.State"
LOG = ROOT / "data" / "use_stuck_ram_trace.jsonl"


def snap(client: BizHawkClient, *, label: str, frames: int) -> dict:
    ram = client.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
            ("player_hp", PLAYER_HP, "u16"),
        ]
    )
    mode = int(ram["game_mode"])
    gs = int(ram["game_state"])
    row = {
        "label": label,
        "cum_frames": frames,
        "game_mode": f"0x{mode:02X}",
        "game_state": f"0x{gs:08X}",
        "hp": int(ram["player_hp"]),
        "in_control": bool(mode & 0x80),
        "outside": outside_gameplay_reason(ram, episode_start_hp=96),
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(
        f"[stuck] f={frames:4d} {label:<22} gm={row['game_mode']} "
        f"gs={row['game_state']} ctrl={row['in_control']} outside={row['outside']!r}",
        flush=True,
    )
    return row


def main() -> int:
    if LOG.exists():
        LOG.unlink()
    port = 7777
    client = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    client.start_server()
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    frames = 0
    try:
        client.wait_for_client()
        client.load_savestate(str(STATE.resolve()))
        client.frameadvance(5)
        snap(client, label="reset", frames=frames)

        died, f, cursor = open_item_screen(client, prev_hp=96, episode_start_hp=96)
        frames += f
        snap(client, label="after_open_item", frames=frames)

        died, f, cursor = _navigate_slot(
            client, cursor, 2, prev_hp=96, episode_start_hp=96
        )
        frames += f
        snap(client, label="after_nav_spray_slot2", frames=frames)

        died, f = _pick_submenu_entry(
            client, "use", prev_hp=96, episode_start_hp=96
        )
        frames += f
        snap(client, label="after_pick_USE_submenu", frames=frames)

        died, f = close_item_screen(client, prev_hp=96, episode_start_hp=96)
        frames += f
        snap(client, label="after_close_item_screen", frames=frames)

        print(f"[stuck] TOTAL_FRAMES={frames} (close loop alone={f})", flush=True)
        if not snap(client, label="final", frames=frames)["in_control"]:
            print("[stuck] CONFIRMED: still in menu after macro — cross spam did nothing useful", flush=True)
    finally:
        try:
            client.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
