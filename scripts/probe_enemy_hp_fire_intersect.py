"""Intersect u16 drops across multiple beretta shots (noise rejected)."""

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
from re1_rl.env import ACTION_NAMES, RE1Env
from re1_rl.game_session import options_menu_from_ram
from re1_rl.memory_map import ENEMY_TABLE_BASE, PS1_MAINRAM_BASE
from re1_rl.options_menu_macro import dismiss_options_menu, read_options_ram

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
OUT = ROOT / "data" / "enemy_hp_fire_intersect.json"

MAINRAM_SIZE = 0x200000
CHUNK = 0x10000


def read_mainram(client: BizHawkClient) -> list[int]:
    out: list[int] = []
    for off in range(0, MAINRAM_SIZE, CHUNK):
        out.extend(
            client.read_block(PS1_MAINRAM_BASE + off, min(CHUNK, MAINRAM_SIZE - off))
        )
    return out


def u16_at(data: list[int], index: int) -> int:
    return data[index] | (data[index + 1] << 8)


def plausible_drops(before: list[int], after: list[int]) -> set[int]:
    """Return set of PS1 addresses (even) with zombie-like u16 drops."""
    out: set[int] = set()
    n = min(len(before), len(after)) - 1
    for i in range(0, n, 2):
        b = u16_at(before, i)
        a = u16_at(after, i)
        if a >= b or b == 0:
            continue
        delta = b - a
        if 5 <= b <= 300 and 5 <= delta <= 60:
            out.add(PS1_MAINRAM_BASE + i)
    return out


def newest_quicksave() -> Path:
    return sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[0]


def resync(env: RE1Env) -> None:
    env._sticky_input.reset()
    env._frame_stack = []
    rgb = env.bridge.screenshot()
    env._push_frame(rgb)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7794)
    ap.add_argument("--shots", type=int, default=3)
    args = ap.parse_args()
    state_path = newest_quicksave()
    print(f"[intersect] {state_path.name} port={args.port}", flush=True)

    bridge = BizHawkClient(port=args.port, timeout=300.0, connect_timeout=120.0)
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
    try:
        bridge.wait_for_client()
        bridge.set_speed(100)
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
        if options_menu_from_ram(ram):
            dismiss_options_menu(
                bridge, prev_hp=ram["player_hp"], episode_start_hp=ram["player_hp"]
            )
        resync(env)
        print(
            f"[intersect] room={env._prev_state.get('room_id')} "
            f"facing={env._prev_state.get('facing')} "
            f"pos=({env._prev_state.get('x')},{env._prev_state.get('z')}) "
            f"weapon={env._prev_state.get('equipped_weapon_id')}",
            flush=True,
        )

        # Noise baseline: noop then diff
        before_noise = read_mainram(bridge)
        env.step(ACTION_NAMES.index("noop"))
        after_noise = read_mainram(bridge)
        noise = plausible_drops(before_noise, after_noise)
        print(f"[intersect] noop noise drops={len(noise)}", flush=True)

        per_shot: list[set[int]] = []
        for i in range(args.shots):
            before = read_mainram(bridge)
            _, rew, term, trunc, info = env.step(ATTACK_ACTION)
            after = read_mainram(bridge)
            drops = plausible_drops(before, after) - noise
            per_shot.append(drops)
            st = info.get("state") or {}
            print(
                f"[intersect] shot={i} rew={rew:+.4f} "
                f"env_dmg={st.get('enemy_damage')} env_kills={st.get('enemy_kills')} "
                f"ammo={st.get('ammo_spent')} candidates={len(drops)}",
                flush=True,
            )
            if term:
                break

        common = set.intersection(*per_shot) if per_shot else set()
        print(f"[intersect] common across {len(per_shot)} shots: {len(common)}", flush=True)
        ranked = sorted(common, key=lambda a: abs(a - int(ENEMY_TABLE_BASE or 0)))
        for addr in ranked[:50]:
            print(f"  0x{addr:08X}  dist_cand={abs(addr - int(ENEMY_TABLE_BASE or 0))}", flush=True)

        # Check if any common addr sits on 0x18C stride lattice from another
        print("[intersect] 0x18C-pair check among top common:", flush=True)
        top = ranked[:80]
        for i, a in enumerate(top):
            for b in top[i + 1 :]:
                if abs(a - b) % 0x18C == 0 and abs(a - b) // 0x18C <= 5:
                    print(f"  pair 0x{a:08X} 0x{b:08X} slots_apart={abs(a-b)//0x18C}", flush=True)

        OUT.write_text(
            json.dumps(
                {
                    "noise": len(noise),
                    "per_shot": [len(s) for s in per_shot],
                    "common": [f"0x{a:08X}" for a in ranked[:100]],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[intersect] wrote {OUT}", flush=True)
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        try:
            proc.terminate()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
