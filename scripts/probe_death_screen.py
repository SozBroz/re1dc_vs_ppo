"""Spawn Jill via curriculum reset, kill her, capture post-death screen + RAM.

Usage:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_death_screen.py --port 5812
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMUHAWK = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
OUT = ROOT / "data" / "death_screen_probe.json"
SHOT_DIR = ROOT / "data" / "death_screen_hunt"

from re1_rl.game_session import outside_gameplay_reason  # noqa: E402
from re1_rl.memory_map import (  # noqa: E402
    CHARACTER_ID,
    GAME_MODE,
    GAME_STATE,
    MESSAGE_FLAG,
    MENU_ROOM_ID,
    PLAYER_HP,
    ROOM_ID,
    SCENE_FLAG,
    STAGE_ID,
)


def _poll(bridge) -> dict[str, int]:
    raw = bridge.read_ram(
        [
            ("game_state", GAME_STATE, "u32"),
            ("game_mode", GAME_MODE, "u8"),
            ("scene_flag", SCENE_FLAG, "u8"),
            ("msg_flag", MESSAGE_FLAG, "u8"),
            ("stage_id", STAGE_ID, "u8"),
            ("room_id", ROOM_ID, "u8"),
            ("character_id", CHARACTER_ID, "u8"),
            ("player_hp", PLAYER_HP, "u16"),
        ]
    )
    return {k: int(raw[k]) for k in raw}


def _snap(bridge, label: str, *, episode_start_hp: int, trace: list) -> None:
    ram = _poll(bridge)
    reason = outside_gameplay_reason(
        {
            "player_hp": ram["player_hp"],
            "stage_id": ram["stage_id"],
            "room_id": ram["room_id"],
            "character_id": ram["character_id"],
            "game_mode": ram["game_mode"],
            "game_state": ram["game_state"],
            "msg_flag": ram["msg_flag"],
            "scene_flag": ram["scene_flag"],
        },
        episode_start_hp=episode_start_hp,
    )
    row = {
        "label": label,
        "game_state_hex": f"0x{ram['game_state']:08X}",
        "game_mode": ram["game_mode"],
        "scene_flag": ram["scene_flag"],
        "msg_flag": ram["msg_flag"],
        "stage_id": ram["stage_id"],
        "room_id": ram["room_id"],
        "character_id": ram["character_id"],
        "player_hp": ram["player_hp"],
        "outside_gameplay": reason,
    }
    trace.append(row)
    print(
        f"[death_probe] {label}: hp={ram['player_hp']} room={ram['room_id']} "
        f"gs=0x{ram['game_state']:08X} mode=0x{ram['game_mode']:02X} "
        f"outside={reason!r}",
        flush=True,
    )
    bridge.screenshot(str(SHOT_DIR / f"{label}.png"))


def main() -> int:
    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.env import RE1Env

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5812)
    ap.add_argument("--speed", type=int, default=400)
    args = ap.parse_args()

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    shot = str(SHOT_DIR / f"live_{args.port}.png")
    bridge = BizHawkClient(port=args.port, timeout=300.0, screenshot_path=shot)
    bridge.start_server()

    proc = subprocess.Popen(
        [
            str(EMUHAWK),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
        ],
        cwd=str(ROOT),
    )
    trace: list[dict] = []
    try:
        bridge.wait_for_client()
        bridge.set_speed(int(args.speed))

        env = RE1Env(
            curriculum_path=ROOT / "curriculum" / "m0_dining_to_main_hall.json",
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        obs, info = env.reset()
        state = info.get("state", {})
        ep_hp = int(state.get("hp", 0) or env._episode_start_hp)
        _snap(bridge, "spawned", episode_start_hp=ep_hp, trace=trace)

        # Method 1: engine death via HP drain (instant)
        bridge.write_ram([("player_hp", PLAYER_HP, "u16", 0)])
        for wait in (30, 120, 300, 600, 1200):
            bridge.frameadvance(wait)
            _snap(bridge, f"hp_zero_adv_{wait}", episode_start_hp=ep_hp, trace=trace)

        # Method 2: env death step (what training sees)
        env._prev_hp = max(ep_hp, 96)
        env._episode_start_hp = max(ep_hp, 96)
        obs2, rew, term, trunc, step_info = env._death_step(0, died_during_skip=False, died_during_step=True)
        ram = _poll(bridge)
        trace.append(
            {
                "label": "env_death_step",
                "terminated": term,
                "truncated": trunc,
                "reward": float(rew),
                "info_dead": step_info.get("died_during_step"),
                "game_state_hex": f"0x{ram['game_state']:08X}",
                "game_mode": ram["game_mode"],
                "room_id": ram["room_id"],
                "player_hp": ram["player_hp"],
            }
        )
        bridge.screenshot(str(SHOT_DIR / "env_death_step.png"))

        # Method 3: reload fresh Jill and try Continue screen via cross mash after hp0
        bridge.load_savestate(str(ROOT / "states" / "jill_control_fresh.State"))
        bridge.frameadvance(4)
        bridge.write_ram([("player_hp", PLAYER_HP, "u16", 0)])
        bridge.frameadvance(180)
        for i in range(8):
            bridge.step(buttons={"cross": True}, n=8)
            bridge.frameadvance(30)
            _snap(bridge, f"continue_mash_{i}", episode_start_hp=96, trace=trace)

        payload = {
            "menu_room_id": MENU_ROOM_ID,
            "episode_start_hp": ep_hp,
            "trace": trace,
        }
        OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[death_probe] wrote {OUT}", flush=True)
        env.close()
        return 0
    finally:
        try:
            bridge.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
