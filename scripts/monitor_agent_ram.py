"""Single training agent with per-step RAM + mask logging (probe port 7777 only).

Runs until Ctrl+C. You watch stdout; yell when it does something dumb.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.action_mask import COMBINE_ACTION, EQUIP_ACTION, USE_ACTION
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.game_session import outside_gameplay_reason
from re1_rl.ram_skip import item_inventory_screen_from_ram, needs_skip_from_ram
from re1_rl.memory_map import (
    EQUIPPED_SLOT_INDEX_1BASED,
    EQUIPPED_WEAPON_ID,
    GAME_MODE,
    GAME_STATE,
    MESSAGE_FLAG,
    PLAYER_HP,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
LOG = ROOT / "data" / "agent_ram_monitor.jsonl"


def _round_nums(obj, ndigits: int = 5):
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return round(obj, ndigits)
    if hasattr(obj, "item") and type(obj).__module__ == "numpy":
        try:
            return round(float(obj.item()), ndigits)
        except (TypeError, ValueError):
            return obj
    if isinstance(obj, dict):
        return {k: _round_nums(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_nums(v, ndigits) for v in obj]
    return obj


def _snap(bridge: BizHawkClient, *, ep_hp: int) -> dict:
    from re1_rl.item_box import read_inventory
    from re1_rl.weapon_equip import read_equipped_slot_0based

    ram = bridge.read_ram(
        [
            ("game_mode", GAME_MODE, "u8"),
            ("game_state", GAME_STATE, "u32"),
            ("msg_flag", MESSAGE_FLAG, "u8"),
            ("scene_flag", SCENE_FLAG, "u8"),
            ("player_hp", PLAYER_HP, "u16"),
            ("room_id", ROOM_ID, "u8"),
            ("stage_id", STAGE_ID, "u8"),
            ("equipped_weapon_id", EQUIPPED_WEAPON_ID, "u8"),
            ("equipped_slot_1based", EQUIPPED_SLOT_INDEX_1BASED, "u8"),
        ]
    )
    mode = int(ram.get("game_mode", 0))
    inv = read_inventory(bridge)
    return {
        "game_mode": f"0x{mode:02X}",
        "game_state": f"0x{int(ram.get('game_state', 0)):08X}",
        "hp": int(ram.get("player_hp", 0)),
        "room": int(ram.get("room_id", 0)),
        "stage": int(ram.get("stage_id", 0)),
        "msg_flag": int(ram.get("msg_flag", 0)),
        "scene_flag": int(ram.get("scene_flag", 0)),
        "equipped_weapon_id": int(ram.get("equipped_weapon_id", 0)),
        "equipped_slot_0b": read_equipped_slot_0based(bridge),
        "inventory": [(int(i), int(q)) for i, q in inv],
        "in_control": bool(mode & 0x80),
        "needs_skip": needs_skip_from_ram(ram),
        "item_inv": item_inventory_screen_from_ram(ram),
        "outside": outside_gameplay_reason(ram, episode_start_hp=ep_hp),
    }


def _mask_bits(env: RE1Env) -> dict:
    m = env.unwrapped.action_masks()
    return {
        "use": bool(m[USE_ACTION]),
        "equip": bool(m[EQUIP_ACTION]),
        "combine": bool(m[COMBINE_ACTION]),
        "legal": int(m.sum()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    port = int(args.port)

    if LOG.exists():
        LOG.unlink()

    bridge = BizHawkClient(port=port, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    print(f"[agent-ram] port {port} — ONE agent, masked random policy", flush=True)
    print(f"[agent-ram] log -> {LOG}", flush=True)
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={port}"],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    rng = np.random.default_rng(int(args.seed))
    step_i = 0
    session_reward = 0.0
    episode_reward = 0.0
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
        env = RE1Env(
            curriculum_path=CURRICULUM,
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=True,
        )
        obs, _ = env.reset()
        ep_hp = int(getattr(env.unwrapped, "_episode_start_hp", 96) or 96)
        ram0 = _snap(bridge, ep_hp=ep_hp)
        print(f"[agent-ram] reset {ram0} mask={_mask_bits(env)}", flush=True)

        while True:
            m = env.unwrapped.action_masks()
            legal = np.flatnonzero(m)
            if len(legal) == 0:
                ram = _snap(bridge, ep_hp=ep_hp)
                print(f"[agent-ram] !! NO LEGAL ACTIONS {ram}", flush=True)
                time.sleep(0.5)
                continue

            action = int(rng.choice(legal))
            pre = _snap(bridge, ep_hp=ep_hp)
            pre_mask = _mask_bits(env)
            t0 = time.perf_counter()
            obs, rew, term, trunc, info = env.step(action)
            dt = time.perf_counter() - t0
            episode_reward += float(rew)
            session_reward += float(rew)
            post = _snap(bridge, ep_hp=ep_hp)
            emu_f = int(info.get("state", {}).get("step_emulated_frames", 0))
            magic = info.get("magic_report") or {}
            row = _round_nums(
                {
                    "step": step_i,
                    "action": action,
                    "action_name": ACTION_NAMES[action],
                    "reward": float(rew),
                    "episode_reward": episode_reward,
                    "session_reward": session_reward,
                    "rooms_visited": sorted(env.unwrapped._progress.visited_rooms),
                    "emu_frames": emu_f,
                    "wall_s": dt,
                    "ram_pre": pre,
                    "ram_post": post,
                    "mask_pre": pre_mask,
                    "mask_post": _mask_bits(env),
                    "magic_report": magic,
                    "use_phase": info.get("use_phase"),
                    "equip_phase": info.get("equip_phase"),
                    "combine_phase": info.get("combine_phase"),
                    "terminated": term,
                    "truncated": trunc,
                }
            )
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")

            flags = []
            if emu_f > 50:
                flags.append(f"SLOW={emu_f}f")
            if magic.get("reason"):
                flags.append(f"magic={magic['reason']}")
            if info.get("use_phase"):
                flags.append(f"use_ph={info['use_phase']}")
            if info.get("equip_phase"):
                flags.append(f"eq_ph={info['equip_phase']}")
            if not post["in_control"]:
                flags.append("NOT_CTRL")
            if post.get("item_inv"):
                flags.append("ITEM_MENU")
            elif post.get("needs_skip"):
                flags.append("NEEDS_SKIP")
            bd = info.get("reward_breakdown") or {}
            if bd.get("new_cutscene", 0) > 0:
                flags.append("CUTSCENE_REW")
            if bd.get("new_room", 0) > 0:
                flags.append("NEW_ROOM")
            if post["outside"]:
                flags.append(f"outside={post['outside']}")

            print(
                f"[agent-ram] #{step_i:4d} {ACTION_NAMES[action]:<16} "
                f"gm={post['game_mode']} hp={post['hp']:3d} "
                f"eq=0x{post['equipped_weapon_id']:02X} "
                f"emu_f={emu_f:3d} rew={float(rew):+.5f} "
                f"{' '.join(flags)}",
                flush=True,
            )
            print(
                f"[agent-ram]        net_reward episode={episode_reward:+.5f} "
                f"session={session_reward:+.5f} "
                f"rooms={len(env.unwrapped._progress.visited_rooms)}",
                flush=True,
            )

            step_i += 1
            if term or trunc:
                print(
                    f"[agent-ram] episode end term={term} trunc={trunc} "
                    f"net={episode_reward:+.5f} — reset",
                    flush=True,
                )
                obs, _ = env.reset()
                ep_hp = int(getattr(env.unwrapped, "_episode_start_hp", 96) or 96)
                episode_reward = 0.0
                step_i = 0

    except KeyboardInterrupt:
        print("[agent-ram] stopped", flush=True)
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError):
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
