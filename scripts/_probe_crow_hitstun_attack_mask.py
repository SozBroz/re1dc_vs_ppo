"""Probe newest QuickSave: attack masked while Jill is under crow attack?"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import (  # noqa: E402
    ATTACK_ACTION,
    ATTACK_DOWN_ACTION,
    ATTACK_UP_ACTION,
    ATTACK_DOWN_ACTION,
    action_mask,
)
from re1_rl.bizhawk_bridge import BizHawkClient  # noqa: E402
from re1_rl.enemy_combat import combat_enemy_count, format_enemy_table  # noqa: E402
from re1_rl.env import ACTION_NAMES  # noqa: E402
from re1_rl.item_box import read_inventory  # noqa: E402
from re1_rl.knife_macro import (  # noqa: E402
    classify_knife_anim,
    is_knife_foreign_anim,
    knife_action_ready,
    read_knife_hooks,
)
from re1_rl.memory_map import (  # noqa: E402
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    GAME_STATE,
    PLAYER_HP,
    PLAYER_X,
    PLAYER_Z,
    ROOM_ID,
    STAGE_ID,
    decode_enemy_table,
    enemy_table_fields,
)
from re1_rl.ram_skip import in_control_from_ram, pause_menu_tree_from_ram  # noqa: E402
from re1_rl.weapon_equip import policy_inventory  # noqa: E402

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
PORT = 7821
OUT = ROOT / "data" / "_crow_hitstun_attack_mask_probe.json"

# Crow active_byte from room_enemies notes / prior QS0 hunt.
CROW_IDLE_ACTIVE = 0x04
CROW_FLYING_ACTIVE = 0x1C


def newest_state() -> Path:
    cands = list(STATE_DIR.glob("*.State")) + list((ROOT / "states").glob("*.State"))
    cands = [p for p in cands if p.is_file() and not p.name.endswith(".bak")]
    if not cands:
        raise FileNotFoundError("no .State files found")
    return max(cands, key=lambda p: p.stat().st_mtime)


def exit_pause(bridge: BizHawkClient) -> bool:
    for _ in range(40):
        ram = bridge.read_ram(
            [("game_mode", GAME_MODE, "u8"), ("game_state", GAME_STATE, "u32")]
        )
        if in_control_from_ram(ram) and not pause_menu_tree_from_ram(ram):
            return True
        for buttons in ({"start": True}, {"triangle": True}, {"circle": True}):
            bridge.step(buttons, n=2)
            bridge.step({}, n=8)
    return False


def snapshot(bridge: BizHawkClient) -> dict:
    pose = bridge.read_ram(
        [
            ("hp", PLAYER_HP, "u16"),
            ("player_x", PLAYER_X, "s16"),
            ("player_z", PLAYER_Z, "s16"),
            ("stage_id", STAGE_ID, "u8"),
            ("room_byte", ROOM_ID, "u8"),
            ("wid", EQUIPPED_WEAPON_ID, "u8"),
            ("eq_slot", EQUIPPED_SLOT_INDEX_1BASED, "u8"),
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
        ]
        + enemy_table_fields()
    )
    anim, aux, recovery = read_knife_hooks(bridge)
    label = classify_knife_anim(anim, aux, recovery)
    ready = knife_action_ready(anim, aux, recovery)
    foreign = is_knife_foreign_anim(anim, aux, recovery)
    enemies = decode_enemy_table(pose)
    # Enrich crow-ish flags from active_byte hunt notes.
    for e in enemies:
        ab = int(e.get("active_byte", 0))
        e["flying"] = 1 if ab == CROW_FLYING_ACTIVE else 0
        e["crow_idle"] = 1 if ab == CROW_IDLE_ACTIVE else 0
        e["is_crow_guess"] = 1 if ab in (CROW_IDLE_ACTIVE, CROW_FLYING_ACTIVE) else 0

    gun_n = combat_enemy_count(enemies)
    knife_n = combat_enemy_count(enemies, knife=True)
    inv = policy_inventory(read_inventory(bridge))
    wid = int(pose["wid"])
    slot_1b = int(pose["eq_slot"])
    slot_0b = slot_1b - 1 if slot_1b > 0 else None
    in_ctrl = in_control_from_ram(pose)
    mask = action_mask(
        len(ACTION_NAMES),
        None,
        player_anim=anim,
        player_aux=aux,
        player_recovery=recovery,
        equipped_weapon_id=wid,
        equipped_slot_0based=slot_0b,
        inventory=inv,
        alive_enemies_in_room=gun_n,
        knife_enemies_near=knife_n,
        gun_enemies_near=gun_n,
        mask_combat_without_enemies=True,
        in_control=in_ctrl,
    )
    room = f"{int(pose['stage_id']) + 1}{int(pose['room_byte']):02X}"
    combat_bits = {
        "attack_down": bool(mask[ATTACK_DOWN_ACTION]),
        "attack": bool(mask[ATTACK_ACTION]),
        "attack_up": bool(mask[ATTACK_UP_ACTION]),
        "attack_down": bool(mask[ATTACK_DOWN_ACTION]),
    }
    flying = sum(int(e.get("flying", 0)) for e in enemies)
    crow_idle = sum(int(e.get("crow_idle", 0)) for e in enemies)
    return {
        "room": room,
        "hp": int(pose["hp"]),
        "x": int(pose["player_x"]),
        "z": int(pose["player_z"]),
        "in_control": in_ctrl,
        "game_mode": int(pose["game_mode"]),
        "game_state": f"0x{int(pose['game_state']):08X}",
        "equipped_weapon_id": wid,
        "equipped_slot_0based": slot_0b,
        "player_anim": anim,
        "player_aux": aux,
        "player_recovery": recovery,
        "anim_label": label,
        "knife_action_ready": ready,
        "is_knife_foreign_anim": foreign,
        "enemies": enemies,
        "enemy_table": format_enemy_table(enemies),
        "flying_crows": flying,
        "idle_crows": crow_idle,
        "gun_enemies_near": gun_n,
        "knife_enemies_near": knife_n,
        "combat_mask": combat_bits,
        "legal_actions": [ACTION_NAMES[i] for i, v in enumerate(mask) if v],
    }


def verdict_for(snap: dict) -> str:
    under_hit = bool(snap["is_knife_foreign_anim"]) or (
        snap["anim_label"] == "foreign"
    )
    attack_legal = bool(snap["combat_mask"]["attack"]) or bool(
        snap["combat_mask"]["knife_swing"]
    )
    crowish = int(snap["flying_crows"]) > 0 or int(snap["idle_crows"]) > 0
    if under_hit and not attack_legal:
        return "attack_masked_correctly"
    if under_hit and attack_legal:
        return "BUG_legal_while_under_attack"
    if crowish and not under_hit:
        return "savestate_not_in_crow_attack"
    if not crowish and not under_hit:
        return "savestate_not_in_crow_attack"
    return "savestate_not_in_crow_attack"


def main() -> int:
    state = newest_state()
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.stat().st_mtime))
    print(f"STATE={state}", flush=True)
    print(f"mtime={mtime}", flush=True)

    bridge = BizHawkClient(
        port=PORT,
        timeout=120.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / "_crow_hitstun_probe.png"),
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
    report: dict = {
        "state_path": str(state),
        "state_mtime": mtime,
        "port": PORT,
        "mask_gate": (
            "action_mask uses knife_action_ready(player_anim, aux, recovery); "
            "foreign/hurt anims fail the whitelist → knife/attack/attack_up/"
            "attack_down False. Crow flying via active_byte==0x1C."
        ),
    }
    try:
        time.sleep(8)
        bridge.wait_for_client()
        bridge.load_savestate(str(state))
        bridge.frameadvance(2)
        exited = exit_pause(bridge)
        report["exit_pause_ok"] = exited

        at_load = snapshot(bridge)
        report["at_load"] = at_load
        print(
            f"LOAD room={at_load['room']} hp={at_load['hp']} "
            f"anim=0x{at_load['player_anim']:02X}/0x{at_load['player_aux']:02X}/"
            f"r={at_load['player_recovery']} label={at_load['anim_label']} "
            f"ready={at_load['knife_action_ready']} foreign={at_load['is_knife_foreign_anim']} "
            f"enemies={at_load['enemy_table']} flying={at_load['flying_crows']} "
            f"mask={at_load['combat_mask']}",
            flush=True,
        )

        frames: list[dict] = []
        saw_foreign = bool(at_load["is_knife_foreign_anim"])
        saw_hp_drop = False
        hp0 = int(at_load["hp"])
        for i in range(30):
            bridge.frameadvance(2)
            snap = snapshot(bridge)
            frames.append(
                {
                    "t": i,
                    "hp": snap["hp"],
                    "anim": snap["player_anim"],
                    "aux": snap["player_aux"],
                    "recovery": snap["player_recovery"],
                    "label": snap["anim_label"],
                    "ready": snap["knife_action_ready"],
                    "foreign": snap["is_knife_foreign_anim"],
                    "flying": snap["flying_crows"],
                    "combat_mask": snap["combat_mask"],
                }
            )
            if snap["is_knife_foreign_anim"] or snap["anim_label"] == "foreign":
                saw_foreign = True
            if int(snap["hp"]) < hp0:
                saw_hp_drop = True
            # Stop early once we catch a clear mid-hit sample.
            if saw_foreign and not snap["combat_mask"]["attack"]:
                report["mid_hit_sample"] = snap
                break
            if saw_foreign and snap["combat_mask"]["attack"]:
                report["mid_hit_sample"] = snap
                break

        report["frame_probe"] = frames
        report["saw_foreign_anim"] = saw_foreign
        report["saw_hp_drop"] = saw_hp_drop

        # Prefer mid-hit sample for verdict; else at_load.
        focus = report.get("mid_hit_sample") or at_load
        report["verdict_focus"] = "mid_hit" if "mid_hit_sample" in report else "at_load"
        report["verdict"] = verdict_for(focus)
        # Also record at_load verdict explicitly.
        report["verdict_at_load"] = verdict_for(at_load)

        print(f"VERDICT={report['verdict']} focus={report['verdict_focus']}", flush=True)
        print(f"saw_foreign={saw_foreign} saw_hp_drop={saw_hp_drop}", flush=True)
        if "mid_hit_sample" in report:
            m = report["mid_hit_sample"]
            print(
                f"MIDHIT anim=0x{m['player_anim']:02X} label={m['anim_label']} "
                f"ready={m['knife_action_ready']} mask={m['combat_mask']}",
                flush=True,
            )

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"WROTE {OUT}", flush=True)
        return 0 if report["verdict"] != "BUG_legal_while_under_attack" else 2
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            bridge.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        print("torn down probe EmuHawk", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
