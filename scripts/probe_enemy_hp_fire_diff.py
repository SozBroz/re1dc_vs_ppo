"""Auto enemy-HP hunt: load QuickSave, snapshot MainRAM, fire, diff u16 drops.

Finds plausible enemy HP addresses when the configured ENEMY_TABLE_BASE is wrong.
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
from re1_rl.game_session import options_menu_from_ram
from re1_rl.memory_map import ENEMY_TABLE_BASE, PS1_MAINRAM_BASE
from re1_rl.options_menu_macro import dismiss_options_menu, read_options_ram

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"
OUT = ROOT / "data" / "enemy_hp_fire_diff.json"

MAINRAM_SIZE = 0x200000
CHUNK = 0x10000


def read_mainram(client: BizHawkClient) -> list[int]:
    out: list[int] = []
    for off in range(0, MAINRAM_SIZE, CHUNK):
        out.extend(client.read_block(PS1_MAINRAM_BASE + off, min(CHUNK, MAINRAM_SIZE - off)))
    return out


def u16_at(data: list[int], index: int) -> int:
    if index + 1 >= len(data):
        return 0
    return data[index] | (data[index + 1] << 8)


def newest_quicksave() -> Path:
    states = sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return states[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7793)
    ap.add_argument("--shots", type=int, default=1)
    args = ap.parse_args()
    state_path = newest_quicksave()
    print(f"[hp-hunt] state={state_path.name} port={args.port}", flush=True)

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
            bridge.frameadvance(4)

        print("[hp-hunt] snapshot BEFORE (2 MiB MainRAM)…", flush=True)
        t0 = time.time()
        before = read_mainram(bridge)
        print(f"[hp-hunt] before done in {time.time() - t0:.1f}s", flush=True)

        for i in range(args.shots):
            _, rew, term, trunc, info = env.step(ATTACK_ACTION)
            st = info.get("state") or {}
            print(
                f"[hp-hunt] shot={i} rew={rew:+.4f} dmg={st.get('enemy_damage')} "
                f"kills={st.get('enemy_kills')} ammo={st.get('ammo_spent')} "
                f"outcome={(info.get('attack_report') or {}).get('outcome')}",
                flush=True,
            )
            if term:
                break

        print("[hp-hunt] snapshot AFTER…", flush=True)
        t0 = time.time()
        after = read_mainram(bridge)
        print(f"[hp-hunt] after done in {time.time() - t0:.1f}s", flush=True)

        # u16 little-endian decreases in plausible zombie HP band
        drops: list[dict] = []
        n = min(len(before), len(after)) - 1
        for i in range(0, n, 2):
            b = u16_at(before, i)
            a = u16_at(after, i)
            if a >= b or b == 0:
                continue
            delta = b - a
            # Prefer "looks like combat HP": old in 1..400, drop 1..80 (beretta chip)
            if 1 <= b <= 400 and 1 <= delta <= 80:
                addr = PS1_MAINRAM_BASE + i
                drops.append(
                    {
                        "addr": f"0x{addr:08X}",
                        "addr_int": addr,
                        "old": b,
                        "new": a,
                        "delta": delta,
                        "dist_to_candidate": abs(addr - int(ENEMY_TABLE_BASE or 0)),
                    }
                )

        drops.sort(key=lambda d: (d["dist_to_candidate"], -d["delta"]))
        print(f"[hp-hunt] plausible HP drops: {len(drops)}", flush=True)
        for d in drops[:40]:
            print(
                f"  {d['addr']}  {d['old']} -> {d['new']}  (-{d['delta']})  "
                f"dist_cand={d['dist_to_candidate']}",
                flush=True,
            )

        # Also report configured table slots
        base = int(ENEMY_TABLE_BASE or 0)
        print(f"[hp-hunt] configured ENEMY_TABLE_BASE=0x{base:08X} slots:", flush=True)
        for slot in range(6):
            addr = base + slot * 0x18C
            idx = addr - PS1_MAINRAM_BASE
            if 0 <= idx < n:
                print(
                    f"  slot{slot} @0x{addr:08X} before={u16_at(before, idx)} "
                    f"after={u16_at(after, idx)}",
                    flush=True,
                )

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps(
                {
                    "state": state_path.name,
                    "candidate": f"0x{base:08X}",
                    "n_drops": len(drops),
                    "top": drops[:80],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[hp-hunt] wrote {OUT}", flush=True)
        return 0 if drops else 3
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
