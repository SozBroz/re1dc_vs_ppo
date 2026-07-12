"""Verify Gameshark zombie HP 0x800C532C vs fire on latest QuickSave."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.action_mask import ATTACK_ACTION
from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import RE1Env
from re1_rl.game_session import options_menu_from_ram
from re1_rl.options_menu_macro import dismiss_options_menu, read_options_ram

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE = sorted(
    (ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State").glob("*.QuickSave*.State"),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)[0]
CURRICULUM = ROOT / "curriculum" / "m0_dining_to_main_hall.json"

# Gameshark: First Zombie Infinite Health / Zombie Health (House)
GS_ZOMBIE_HP = 0x800C532C
# Probe a window of u16s around it + a few alternate strides
PROBE = []
for off in range(-0x40, 0x200, 2):
    PROBE.append(("hp_%+d" % off, GS_ZOMBIE_HP + off, "u16"))
# Also old wrong base slot0
PROBE.append(("old_slot0", 0x801141FC, "u16"))
PROBE.append(("old_slot4", 0x8011482C, "u16"))


def main() -> int:
    port = 7796
    print(f"state={STATE.name} watching 0x{GS_ZOMBIE_HP:08X}", flush=True)
    bridge = BizHawkClient(port=port, timeout=180.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [
            str(EMU),
            str(ROM),
            f"--lua={LUA}",
            "--socket_ip=127.0.0.1",
            f"--socket_port={port}",
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
        bridge.load_savestate(str(STATE))
        bridge.frameadvance(8)
        ram = read_options_ram(bridge)
        if options_menu_from_ram(ram):
            dismiss_options_menu(
                bridge, prev_hp=ram["player_hp"], episode_start_hp=ram["player_hp"]
            )
        # resync prev
        env._prev_state = env._read_state()
        env._prev_hp = int(env._prev_state["hp"])

        def snap(label: str) -> dict[str, int]:
            r = bridge.read_ram(PROBE)
            vals = {k: int(v) for k, v in r.items()}
            interesting = {
                k: v
                for k, v in vals.items()
                if k.startswith("old_") or (1 <= v <= 200)
            }
            print(f"[{label}] gs_hp={vals.get('hp_+0')} interesting={interesting}", flush=True)
            return vals

        before = snap("before")
        for i in range(5):
            _, rew, term, trunc, info = env.step(ATTACK_ACTION)
            after = snap(f"shot{i}")
            drops = []
            for k, b in before.items():
                a = after[k]
                if a < b and 1 <= b <= 300:
                    drops.append((k, b, a, b - a))
            drops.sort(key=lambda t: -t[3])
            print(
                f"  rew={rew:+.4f} env_dmg={info.get('state', {}).get('enemy_damage')} "
                f"drops={drops[:12]}",
                flush=True,
            )
            before = after
            if term:
                break
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
