"""Time each bootstrap bridge call; run while play_human is NOT using the port."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PORT = 7777
SAVE = PROJECT_ROOT / "states" / "jill_control_fresh.State"
EMUHAWK = PROJECT_ROOT / "tools" / "BizHawk-2.11.1" / "EmuHawk.exe"
ROM = PROJECT_ROOT / "roms" / "Resident Evil - Director's Cut.cue"
LUA = PROJECT_ROOT / "lua" / "re1_client.lua"


def tick(label: str, t0: float) -> float:
    now = time.monotonic()
    print(f"  [{now - t0:6.2f}s] {label}", flush=True)
    return now


def main() -> int:
    from re1_rl.bizhawk_bridge import BizHawkClient
    from re1_rl.ram_skip import RamSkipper, in_control_from_ram, SKIP_POLL_FIELDS

    if not SAVE.is_file():
        print(f"MISSING {SAVE}")
        return 1

    t0 = time.monotonic()
    bridge = BizHawkClient(port=PORT, timeout=60.0, screenshot_path=str(PROJECT_ROOT / "data" / f"_frame_{PORT}.png"))
    bridge.start_server()
    tick("server listening", t0)

    proc = subprocess.Popen(
        [str(EMUHAWK), str(ROM), f"--lua={LUA}", "--socket_ip=127.0.0.1", f"--socket_port={PORT}"],
        cwd=str(EMUHAWK.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    tick("EmuHawk spawned", t0)

    bridge.wait_for_client()
    tick("lua connected", t0)

    bridge.set_speed(100)
    tick("set_speed(100)", t0)

    bridge.load_savestate(str(SAVE.resolve()))
    tick("load_savestate", t0)

    bridge.frameadvance(1)
    tick("frameadvance(1)", t0)

    ram = bridge.read_ram(SKIP_POLL_FIELDS)
    tick(f"read_ram in_control={in_control_from_ram(ram)} room={ram}", t0)

    skip = RamSkipper(bridge, training_speed=100, cutscene_speed=400)
    print("  starting skip_uncontrolled(max_frames=12000)...", flush=True)
    n = skip.skip_uncontrolled(max_frames=12000)
    tick(f"skip_uncontrolled done burned={n}", t0)

    try:
        bridge.quit()
    except Exception:
        pass
    bridge.close()
    proc.terminate()
    tick("cleanup", t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
