"""Live verify: unpause crow QS0 → flying crow unlocks attack macros."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import ATTACK_ACTION, ATTACK_DOWN_ACTION, action_mask
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.enemy_combat import combat_enemy_count, format_enemy_table
from re1_rl.env import ACTION_NAMES
from re1_rl.memory_map import (
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    GAME_STATE,
    PLAYER_HP,
    PLAYER_X,
    PLAYER_Z,
    decode_enemy_table,
    enemy_table_fields,
)
from re1_rl.ram_skip import in_control_from_ram, pause_menu_tree_from_ram

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = (
    ROOT
    / "tools"
    / "BizHawk-2.11.1"
    / "PSX"
    / "State"
    / "Resident Evil - Director's Cut (USA).Nymashock.QuickSave0.State"
)
PORT = 7808


def exit_pause(bridge) -> bool:
    for _ in range(40):
        ram = bridge.read_ram(
            [("game_mode", GAME_MODE, "u8"), ("game_state", GAME_STATE, "u32")]
        )
        if in_control_from_ram(ram) and not pause_menu_tree_from_ram(ram):
            return True
        for buttons in ({"start": True}, {"triangle": True}, {"circle": True}):
            bridge.step(buttons, n=2)
            bridge.step({}, n=8)
            ram = bridge.read_ram(
                [("game_mode", GAME_MODE, "u8"), ("game_state", GAME_STATE, "u32")]
            )
            if in_control_from_ram(ram) and not pause_menu_tree_from_ram(ram):
                return True
    return False


def main() -> int:
    bridge = BizHawkClient(
        port=PORT,
        timeout=120.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / "_crow_verify.png"),
        screenshot_mmf=True,
    )
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={PORT}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(8)
        bridge.wait_for_client()
        bridge.load_savestate(str(STATE))
        bridge.frameadvance(2)
        assert exit_pause(bridge), "failed to leave pause menu"

        saw_flying = False
        saw_attack_legal = False
        for i in range(24):
            for _ in range(8):
                bridge.frameadvance(2)
            ram = bridge.read_ram(
                [
                    ("hp", PLAYER_HP, "u16"),
                    ("player_x", PLAYER_X, "s16"),
                    ("player_z", PLAYER_Z, "s16"),
                ]
                + enemy_table_fields()
            )
            enemies = decode_enemy_table(ram)
            flying = [e for e in enemies if int(e.get("flying", 0))]
            idle = [
                e
                for e in enemies
                if int(e.get("is_crow", 0)) and not int(e.get("flying", 0))
            ]
            gun_n = combat_enemy_count(enemies)
            wid = int(bridge.read_ram([("wid", EQUIPPED_WEAPON_ID, "u8")])["wid"]) or 2
            mask = action_mask(
                len(ACTION_NAMES),
                None,
                equipped_weapon_id=wid,
                inventory=[(1, 1), (2, 30)],
                equipped_slot_0based=1 if wid == 2 else 0,
                alive_enemies_in_room=gun_n,
                knife_enemies_near=combat_enemy_count(enemies, knife=True),
                gun_enemies_near=gun_n,
                mask_combat_without_enemies=True,
                in_control=True,
            )
            attack_ok = bool(mask[ATTACK_ACTION])
            knife_ok = bool(mask[ATTACK_DOWN_ACTION])
            print(
                f"t{i} hp={ram['hp']} enemies={format_enemy_table(enemies)} "
                f"flying={len(flying)} idle={len(idle)} gun_near={gun_n} "
                f"attack={attack_ok} knife={knife_ok}",
                flush=True,
            )
            if flying:
                saw_flying = True
            if attack_ok:
                saw_attack_legal = True
                break

        print(
            f"RESULT saw_flying={saw_flying} saw_attack_legal={saw_attack_legal}",
            flush=True,
        )
        return 0 if saw_flying and saw_attack_legal else 1
    finally:
        try:
            bridge.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
