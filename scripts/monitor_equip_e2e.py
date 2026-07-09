"""Single-agent equip/menu debugger: log every input + RAM after each env step.

Uses probe port 7777 only (never fleet ports 5555+). No training fleet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import EQUIP_ACTION, SELECT_SLOT_BASE, USE_ACTION
from re1_rl.attack_macro import read_equipped_weapon
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.memory_map import (
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    MESSAGE_FLAG,
    PLAYER_HP,
    SCENE_FLAG,
)
from re1_rl.weapon_equip import read_equipped_slot_0based, weapon_already_equipped

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
LOG_PATH = ROOT / "data" / "equip_e2e_monitor.jsonl"


def _ram_menu_snapshot(bridge: BizHawkClient) -> dict:
    from re1_rl.item_box import read_inventory

    ram = bridge.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("msg_flag", MESSAGE_FLAG, "u8"),
            ("scene_flag", SCENE_FLAG, "u8"),
            ("player_hp", PLAYER_HP, "u16"),
            ("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8"),
            ("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8"),
        ]
    )
    inv = read_inventory(bridge)
    eq_id = int(ram.get("equipped_weapon_id", 0))
    eq_slot = read_equipped_slot_0based(bridge)
    return {
        "game_mode": f"0x{int(ram.get('game_mode', 0)):02X}",
        "msg_flag": int(ram.get("msg_flag", 0)),
        "scene_flag": int(ram.get("scene_flag", 0)),
        "hp": int(ram.get("player_hp", 0)),
        "equipped_weapon_id": eq_id,
        "equipped_slot_0b": eq_slot,
        "inventory": [(int(i), int(q)) for i, q in inv],
        "in_control": bool(int(ram.get("game_mode", 0)) & 0x80),
    }


def _log(line: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")
    tags = []
    if line.get("magic_report"):
        tags.append(f"magic={line['magic_report'].get('reason')}")
    if line.get("equip_phase") is not None:
        tags.append(f"eq_ph={line['equip_phase']}")
    if line.get("use_phase") is not None:
        tags.append(f"use_ph={line['use_phase']}")
    ram = line.get("ram", {})
    act = line.get("action_name") or line.get("event", "?")
    print(
        f"[e2e] step={line.get('step_i', '-'):>3} "
        f"act={act:<16} "
        f"eq=0x{ram.get('equipped_weapon_id', 0):02X} "
        f"slot={ram.get('equipped_slot_0b')} "
        f"gm={ram.get('game_mode')} "
        f"ctrl={ram.get('in_control')} "
        f"{' '.join(tags)}",
        flush=True,
    )


def _mask_summary(env: RE1Env) -> dict:
    m = env.unwrapped.action_masks()
    names = ACTION_NAMES
    legal = [names[i] for i in range(len(m)) if m[i]]
    return {
        "equip": bool(m[EQUIP_ACTION]),
        "use": bool(m[USE_ACTION]),
        "select_slots": [
            i for i in range(8) if m[SELECT_SLOT_BASE + i]
        ],
        "legal_count": int(m.sum()),
        "legal_sample": legal[:12],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--scripted", action="store_true", help="Force illegal equip torture test")
    ap.add_argument("--masked-steps", type=int, default=40)
    ap.add_argument("--random-steps", type=int, default=0)
    args = ap.parse_args()
    port = int(args.port)

    if LOG_PATH.exists():
        LOG_PATH.unlink()

    bridge = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    print(f"[e2e] port {port} — launching ONE EmuHawk (not fleet)", flush=True)
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    step_i = 0
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        obs, info = env.reset()
        ram0 = _ram_menu_snapshot(bridge)
        _log({
            "event": "reset",
            "step_i": step_i,
            "ram": ram0,
            "mask": _mask_summary(env),
            "inventory_names": info.get("state", {}).get("inventory", []),
        })

        eq_id = read_equipped_weapon(bridge)
        eq_slot = read_equipped_slot_0based(bridge)
        inv_ids = [i for i, _ in ram0["inventory"] if i]
        print(
            f"[e2e] reset: equipped=0x{eq_id:02X} slot={eq_slot} "
            f"inv={[hex(x) for x in inv_ids]} mask={_mask_summary(env)}",
            flush=True,
        )

        def do_step(action: int, label: str = "") -> None:
            nonlocal step_i, obs
            step_i += 1
            pre = _ram_menu_snapshot(bridge)
            pre_mask = _mask_summary(env)
            target_slot = action - SELECT_SLOT_BASE if action >= SELECT_SLOT_BASE else None
            if target_slot is not None and target_slot >= 0:
                inv = pre["inventory"]
                tid = inv[target_slot][0] if target_slot < len(inv) else 0
                pre_mask["would_re_equip"] = weapon_already_equipped(
                    pre["equipped_weapon_id"], int(tid)
                )
            obs, rew, term, trunc, info = env.step(action)
            post = _ram_menu_snapshot(bridge)
            _log({
                "event": label or "step",
                "step_i": step_i,
                "action": action,
                "action_name": ACTION_NAMES[action],
                "reward": float(rew),
                "ram_pre": pre,
                "ram": post,
                "mask_pre": pre_mask,
                "mask_post": _mask_summary(env),
                "magic_report": info.get("magic_report"),
                "equip_phase": info.get("equip_phase"),
                "use_phase": info.get("use_phase"),
                "step_emulated_frames": info.get("state", {}).get("step_emulated_frames"),
                "terminated": term,
                "truncated": trunc,
            })
            if term or trunc:
                print(f"[e2e] episode ended term={term} trunc={trunc}", flush=True)

        if args.scripted:
            # Deliberately illegal: proves env rejects when mask says no.
            do_step(EQUIP_ACTION, "equip_open")
            do_step(SELECT_SLOT_BASE + 0, "equip_select_knife_slot0")
            do_step(EQUIP_ACTION, "equip_open_again")
            do_step(SELECT_SLOT_BASE + 0, "equip_select_knife_slot0_again")
            do_step(EQUIP_ACTION, "equip_open_beretta")
            do_step(SELECT_SLOT_BASE + 1, "equip_select_slot1")
            do_step(EQUIP_ACTION, "equip_open_back_knife")
            do_step(SELECT_SLOT_BASE + 0, "equip_select_knife_slot0_third")

        masked_n = int(args.masked_steps)
        if masked_n > 0:
            import numpy as np

            rng = np.random.default_rng(0)
            for _ in range(masked_n):
                m = env.unwrapped.action_masks()
                legal = np.flatnonzero(m)
                if len(legal) == 0:
                    ram = _ram_menu_snapshot(bridge)
                    print(
                        f"[e2e] no legal actions gm={ram['game_mode']} "
                        f"ctrl={ram['in_control']}",
                        flush=True,
                    )
                    break
                action = int(rng.choice(legal))
                do_step(action, "random_masked")

        print(f"[e2e] log -> {LOG_PATH}", flush=True)
        final = _ram_menu_snapshot(bridge)
        print(f"[e2e] final ram: {json.dumps(final)}", flush=True)
        time.sleep(0.5)
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
