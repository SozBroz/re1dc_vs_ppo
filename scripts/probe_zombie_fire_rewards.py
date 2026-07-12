"""Load newest QuickSave, fire ATTACK repeatedly, verify hit/kill rewards.

Fleet ports untouched (default 7792). Leaves EmuHawk open until script exits.

  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\probe_zombie_fire_rewards.py
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

from re1_rl.action_mask import ATTACK_ACTION
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.enemy_combat import enemy_hp_by_slot
from re1_rl.game_session import options_menu_from_ram
from re1_rl.options_menu_macro import dismiss_options_menu, read_options_ram
from re1_rl.reward import ENEMY_DAMAGE_REWARD, ENEMY_KILL_REWARD

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
OUT = ROOT / "data" / "zombie_fire_rewards_probe.jsonl"


def newest_quicksave() -> Path:
    states = sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not states:
        raise FileNotFoundError(f"no QuickSave*.State under {STATE_DIR}")
    return states[0]


def _resync_env(env: RE1Env) -> dict:
    env._sticky_input.reset()
    env._frame_stack = []
    rgb = env.bridge.screenshot()
    frame_obs = env._push_frame(rgb)
    state = env._read_state()
    env._seed_episode_progress(state)
    env._episode_history.reset(str(state.get("room_id", "")), step=0)
    env._visited.reset()
    env._visited.update(state["room_id"], state["x"], state["z"])
    env._prev_state = state
    env._prev_hp = int(state["hp"])
    env._episode_start_hp = int(state["hp"])
    env._episode_min_hp = int(state["hp"])
    env._step_count = 0
    env._build_obs(frame_obs, state)
    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7792)
    ap.add_argument("--state", type=Path, default=None)
    ap.add_argument("--shots", type=int, default=12)
    ap.add_argument("--speed", type=int, default=200)
    args = ap.parse_args()
    state_path = args.state or newest_quicksave()
    print(
        f"[zombie-fire] state={state_path.name} "
        f"mtime={time.ctime(state_path.stat().st_mtime)} port={args.port}",
        flush=True,
    )
    if OUT.exists():
        OUT.unlink()

    bridge = BizHawkClient(
        port=args.port,
        timeout=180.0,
        connect_timeout=120.0,
        screenshot_path=str(ROOT / "data" / f"_zombie_fire_{args.port}.png"),
        screenshot_mmf=True,
    )
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={args.port}",
            "--gdi",
        ],
        cwd=str(EMU.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    total_dmg = 0
    total_kills = 0
    total_dmg_r = 0.0
    total_kill_r = 0.0
    try:
        bridge.wait_for_client()
        bridge.set_speed(args.speed)
        env = RE1Env(
            curriculum_path=str(CURRICULUM),
            bridge=bridge,
            project_root=ROOT,
            async_cutscene_skip=False,
        )
        env.reset()
        bridge.load_savestate(str(state_path))
        bridge.frameadvance(8)

        ram = read_options_ram(bridge)
        print(
            f"[zombie-fire] after load: room={ram['room_id']} hp={ram['player_hp']} "
            f"gs=0x{ram['game_state']:08X} options={options_menu_from_ram(ram)}",
            flush=True,
        )
        if options_menu_from_ram(ram):
            still, frames, report = dismiss_options_menu(
                bridge,
                prev_hp=ram["player_hp"],
                episode_start_hp=ram["player_hp"],
            )
            print(
                f"[zombie-fire] dismissed OPTIONS still={still} frames={frames} "
                f"report={report}",
                flush=True,
            )
            if still:
                print("[zombie-fire] FAIL: still in OPTIONS", flush=True)
                return 2

        state0 = _resync_env(env)
        enemies0 = enemy_hp_by_slot(state0.get("enemies", []))
        print(
            f"[zombie-fire] synced room={state0.get('room_id')} "
            f"hp={state0.get('hp')} enemies={enemies0} "
            f"eq_weapon={state0.get('equipped_weapon_id')}",
            flush=True,
        )
        if not enemies0:
            print(
                "[zombie-fire] WARN: no living enemies in RAM table — "
                "shots may all miss / no combat reward",
                flush=True,
            )

        print(
            f"[zombie-fire] scales: ENEMY_DAMAGE_REWARD={ENEMY_DAMAGE_REWARD} "
            f"ENEMY_KILL_REWARD={ENEMY_KILL_REWARD}",
            flush=True,
        )

        for i in range(int(args.shots)):
            prev_enemies = enemy_hp_by_slot(env._prev_state.get("enemies", []))
            obs, rew, term, trunc, info = env.step(ATTACK_ACTION)
            state = info.get("state") or {}
            bd = info.get("reward_breakdown") or {}
            dmg = int(state.get("enemy_damage", 0) or 0)
            kills = int(state.get("enemy_kills", 0) or 0)
            report = info.get("attack_report") or {}
            curr_enemies = enemy_hp_by_slot(state.get("enemies", []))
            total_dmg += dmg
            total_kills += kills
            total_dmg_r += float(bd.get("enemy_damage", 0.0))
            total_kill_r += float(bd.get("enemy_kill", 0.0))
            row = {
                "shot": i,
                "reward": float(rew),
                "enemy_damage": dmg,
                "enemy_kills": kills,
                "bd_enemy_damage": float(bd.get("enemy_damage", 0.0)),
                "bd_enemy_kill": float(bd.get("enemy_kill", 0.0)),
                "bd_attack_miss": float(bd.get("attack_miss", 0.0)),
                "ammo_spent": int(state.get("ammo_spent", 0) or 0),
                "outcome": report.get("outcome"),
                "prev_enemies": prev_enemies,
                "curr_enemies": curr_enemies,
                "player_hp": int(state.get("hp", 0) or 0),
                "term": bool(term),
                "trunc": bool(trunc),
            }
            with OUT.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(
                f"[zombie-fire] shot={i} rew={rew:+.4f} "
                f"dmg={dmg} kills={kills} "
                f"bd_dmg={bd.get('enemy_damage', 0):+.4f} "
                f"bd_kill={bd.get('enemy_kill', 0):+.4f} "
                f"miss={bd.get('attack_miss', 0):+.4f} "
                f"ammo={row['ammo_spent']} outcome={row['outcome']} "
                f"enemies {prev_enemies} -> {curr_enemies}",
                flush=True,
            )
            if term or trunc:
                print("[zombie-fire] episode ended", flush=True)
                break
            if total_kills > 0:
                print("[zombie-fire] kill observed — stopping early", flush=True)
                break

        print(
            f"[zombie-fire] TOTAL dmg_hp={total_dmg} kills={total_kills} "
            f"reward_dmg={total_dmg_r:+.4f} reward_kill={total_kill_r:+.4f} "
            f"log={OUT}",
            flush=True,
        )
        if total_dmg <= 0 and total_kills <= 0:
            print(
                "[zombie-fire] FAIL: no enemy HP drop detected — "
                "combat reward path not exercised",
                flush=True,
            )
            return 3
        if total_dmg > 0 and total_dmg_r <= 0:
            print("[zombie-fire] FAIL: damage dealt but enemy_damage reward=0", flush=True)
            return 4
        if total_kills > 0 and total_kill_r <= 0:
            print("[zombie-fire] FAIL: kill but enemy_kill reward=0", flush=True)
            return 5
        print("[zombie-fire] PASS: hit (and kill if any) produced positive rewards", flush=True)
        return 0
    finally:
        try:
            bridge.quit()
        except (OSError, ConnectionError, RuntimeError):
            pass
        try:
            proc.terminate()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
