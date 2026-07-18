"""Load newest QuickSave; input forward every step; watch for shelf push."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.env import ACTION_BUTTON_MAP, ACTION_NAMES
from re1_rl.memory_map import DEFAULT_RAM_FIELDS, IN_CONTROL_MASK
from re1_rl.sticky_input import StickyInputState

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
PORT = 7788
FRAME_SKIP = 8
N_STEPS = 60


def newest() -> Path:
    return sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[0]


def snap(bridge: BizHawkClient) -> dict:
    ram = bridge.read_ram(list(DEFAULT_RAM_FIELDS))
    stage = int(ram["stage_id"])
    room = int(ram["room_id"])
    mode = int(ram["game_mode"])
    return {
        "room": f"{stage + 1}{room:02X}",
        "x": int(ram["player_x"]),
        "z": int(ram["player_z"]),
        "facing": int(ram["player_facing"]),
        "gs": f"0x{int(ram['game_state']):08X}",
        "mode": f"0x{mode:02X}",
        "ctrl": bool(mode & IN_CONTROL_MASK),
    }


def main() -> int:
    state = newest()
    print(f"[shelf] {state.name}", flush=True)
    print(f"[shelf] mtime={time.ctime(state.stat().st_mtime)}", flush=True)

    bridge = BizHawkClient(port=PORT, timeout=300.0, connect_timeout=120.0)
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
    )
    try:
        print(f"[shelf] waiting for EmuHawk :{PORT}...", flush=True)
        bridge.wait_for_client()
        bridge.set_speed(100)
        bridge.load_savestate(str(state.resolve()))
        bridge.frameadvance(4)

        sticky = StickyInputState()
        fwd = ACTION_NAMES.index("forward")
        before = snap(bridge)
        print(f"[shelf] loaded: {before}", flush=True)

        prev = before
        stalled_run = 0
        push_hint = False
        for i in range(1, N_STEPS + 1):
            s, pulse, pulse_hold = sticky.apply(fwd, ACTION_BUTTON_MAP)
            bridge.step(n=FRAME_SKIP, sticky=s, pulse=pulse, pulse_hold=pulse_hold)
            cur = snap(bridge)
            dx = cur["x"] - prev["x"]
            dz = cur["z"] - prev["z"]
            man = abs(dx) + abs(dz)
            gs_chg = cur["gs"] != prev["gs"]
            print(
                f"step {i:02d}: {cur} d=({dx:+d},{dz:+d}) man={man} gs_chg={gs_chg}",
                flush=True,
            )
            if man < 15:
                stalled_run += 1
            else:
                stalled_run = 0
            # Push signature: was stalled, then starts moving again and/or gs changes
            if gs_chg or (stalled_run == 0 and man > 30 and i > 3):
                # only count "unstall" after we had been stalled
                pass
            if gs_chg:
                print(f"[shelf] GS CHANGE at step {i} — likely push/anim", flush=True)
                push_hint = True
            if cur["room"] != before["room"]:
                print(f"[shelf] left room at step {i}", flush=True)
                break
            prev = cur

        final = snap(bridge)
        total = abs(final["x"] - before["x"]) + abs(final["z"] - before["z"])
        print(
            f"[shelf] DONE before={before} final={final} total_man={total} "
            f"gs_changed={final['gs'] != before['gs']} push_hint={push_hint}",
            flush=True,
        )
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
