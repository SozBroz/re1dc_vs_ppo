"""Probe QuickSave1 (botany book UI) — RAM signature + triangle dismiss."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.bizhawk_paths import EMUHAWK, LUA, ROM
from re1_rl.item_box import read_inventory
from re1_rl.memory_map import (
    CAM_ID,
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    GAME_STATE,
    ITEM_IDS,
    PLAYER_HP,
    ROOM_ID,
    STAGE_ID,
)
from re1_rl.ram_skip import (
    in_control_from_ram,
    item_inventory_screen_from_ram,
    message_open_from_ram,
    pause_menu_tree_from_ram,
    scene_active_from_ram,
)

STATE = (
    ROOT
    / "tools"
    / "BizHawk-2.11.1"
    / "PSX"
    / "State"
    / "Resident Evil - Director's Cut (USA).Nymashock.QuickSave1.State"
)
PORT = 7821
OUT = ROOT / "data" / "_botany_book_qs1_probe.json"


def _snap(client: BizHawkClient) -> dict:
    fields = [
        ("game_mode", GAME_MODE, "u8"),
        ("game_state", GAME_STATE, "u32"),
        ("hp", PLAYER_HP, "u16"),
        ("stage_id", STAGE_ID, "u8"),
        ("room_byte", ROOM_ID, "u8"),
        ("cam_id", CAM_ID, "u8"),
        ("wid", EQUIPPED_WEAPON_ID, "u8"),
    ]
    # Also probe common modal / UI bytes near known maps.
    extras = [
        ("u8_800C5110", 0x800C5110, "u8"),
        ("u8_800C5114", 0x800C5114, "u8"),
        ("u8_800C5118", 0x800C5118, "u8"),
        ("u8_800C5120", 0x800C5120, "u8"),
        ("u8_800C5130", 0x800C5130, "u8"),
        ("u8_800C5200", 0x800C5200, "u8"),
        ("u8_800C5210", 0x800C5210, "u8"),
        ("u8_800C5300", 0x800C5300, "u8"),
        ("u8_800C8680", 0x800C8680, "u8"),
        ("u8_800C8684", 0x800C8684, "u8"),
        ("u8_800C8688", 0x800C8688, "u8"),
        ("u8_800C8690", 0x800C8690, "u8"),
        ("u8_800C86A0", 0x800C86A0, "u8"),
        ("u32_800C3000", 0x800C3000, "u32"),
    ]
    ram = client.read_ram(fields + extras)
    out = {k: int(v) for k, v in ram.items()}
    out["room"] = f"{out['stage_id']+1}{out['room_byte']:02X}"
    out["in_control"] = bool(in_control_from_ram(ram))
    out["pause_menu"] = bool(pause_menu_tree_from_ram(ram))
    out["item_screen"] = bool(item_inventory_screen_from_ram(ram))
    out["message_open"] = bool(message_open_from_ram(ram))
    out["scene_active"] = bool(scene_active_from_ram(ram))
    inv = read_inventory(client)
    out["inventory"] = [
        {"id": int(i), "name": ITEM_IDS.get(int(i), f"id_{i}"), "qty": int(q)}
        for i, q in inv
        if int(i)
    ]
    return out


def main() -> int:
    if not STATE.is_file():
        raise FileNotFoundError(STATE)
    client = BizHawkClient(
        port=PORT,
        timeout=120.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / "_botany_book_qs1.png"),
        screenshot_mmf=True,
    )
    client.start_server()
    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
            "--gdi",
        ],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(8)
        client.wait_for_client()
        client.load_savestate(str(STATE))
        client.frameadvance(3)
        before = _snap(client)
        print("BEFORE", json.dumps(before, indent=2))
        client.screenshot()

        # Triangle dismiss probe
        trail = []
        for i in range(40):
            client.step({"triangle": True}, n=2)
            client.step({}, n=4)
            row = _snap(client)
            trail.append(
                {
                    "i": i,
                    "game_mode": row["game_mode"],
                    "game_state": row["game_state"],
                    "in_control": row["in_control"],
                    "message_open": row["message_open"],
                    "scene_active": row["scene_active"],
                    "pause_menu": row["pause_menu"],
                    "item_screen": row["item_screen"],
                }
            )
            if row["in_control"] and not row["message_open"] and not row["pause_menu"]:
                print(f"CLOSED after triangle pulse {i}")
                break
        after = _snap(client)
        print("AFTER", json.dumps(after, indent=2))
        payload = {"state": str(STATE), "before": before, "trail": trail, "after": after}
        OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("wrote", OUT)
        return 0
    finally:
        try:
            client.quit()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
