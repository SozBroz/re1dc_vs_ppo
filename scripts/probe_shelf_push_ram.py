"""Compare RAM while jammed vs actively pushing (newest QuickSave)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import (
    DEFAULT_RAM_FIELDS,
    PLAYER_ACTION_AUX,
    PLAYER_ANIM_STATE,
    PLAYER_RECOVERY_TIMER,
)

EMU = ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = ROOT / "lua" / "re1_client.lua"
STATE_DIR = ROOT / "tools" / "BizHawk-2.11.1" / "PSX" / "State"
PORT = 7788
PUSH_GS = 0x80800044


def newest() -> Path:
    return sorted(
        STATE_DIR.glob("*.QuickSave*.State"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[0]


def snap(bridge: BizHawkClient) -> dict:
    fields = list(DEFAULT_RAM_FIELDS) + [
        ("anim", PLAYER_ANIM_STATE, "u8"),
        ("aux", PLAYER_ACTION_AUX, "u8"),
        ("rec", PLAYER_RECOVERY_TIMER, "u8"),
    ]
    ram = bridge.read_ram(fields)
    return {
        "x": int(ram["player_x"]),
        "z": int(ram["player_z"]),
        "facing": int(ram["player_facing"]),
        "gs": f"0x{int(ram['game_state']):08X}",
        "anim": f"0x{int(ram['anim']):02X}",
        "aux": f"0x{int(ram['aux']):02X}",
        "rec": int(ram["rec"]),
    }


def hold(bridge: BizHawkClient, n: int) -> None:
    bridge.step(
        n=n,
        sticky={"up": True, "down": False, "left": False, "right": False, "square": False},
    )


def main() -> int:
    state = newest()
    print(f"state={state.name} mtime={time.ctime(state.stat().st_mtime)}", flush=True)
    bridge = BizHawkClient(port=PORT, timeout=300.0, connect_timeout=120.0)
    bridge.start_server()
    proc = subprocess.Popen(
        [str(EMU), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={PORT}", "--gdi"],
        cwd=str(EMU.parent),
    )
    try:
        bridge.wait_for_client()
        bridge.set_speed(200)
        bridge.load_savestate(str(state.resolve()))
        bridge.frameadvance(2)
        print(f"idle/load: {snap(bridge)}", flush=True)

        # Phase A: short hold — jammed, not yet pushing (<15 frames)
        hold(bridge, 8)
        jam = snap(bridge)
        print(f"jammed(~8f): {jam}", flush=True)

        # Phase B: continue to push threshold
        hold(bridge, 8)  # total 16
        push = snap(bridge)
        print(f"after+8f (total16): {push}", flush=True)

        # Phase C: keep holding during push slide
        for i in range(5):
            hold(bridge, 4)
            s = snap(bridge)
            print(f"pushing#{i+1}: {s}", flush=True)
            if int(s["gs"], 16) != PUSH_GS and i > 0:
                print("left push GS", flush=True)
                break
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
