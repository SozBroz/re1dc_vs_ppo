"""Live EmuHawk bridge round-trip test.

Run this FIRST (it listens), then launch EmuHawk with --lua=lua/re1_client.lua.
Exercises: ping, read_ram, read_block, buttons, frameadvance, screenshot,
savestate, speed, quit. Prints PASS/FAIL per step and exits nonzero on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import ROOM_ID, STAGE_ID

STATE_PATH = "D:/re1_rl/states/_bridge_test.State"
SHOT_PATH = "D:/re1_rl/data/_bridge_test.png"


def main() -> int:
    client = BizHawkClient(timeout=180.0)
    client.start_server()
    print("listening on 127.0.0.1:5555, waiting for EmuHawk lua client...", flush=True)
    client.wait_for_client()
    print("client connected", flush=True)

    failures = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        status = "PASS" if cond else "FAIL"
        if not cond:
            failures += 1
        print(f"  [{status}] {name} {detail}", flush=True)

    pong = client.ping(42)
    check("ping", pong == 42, f"pong={pong}")

    frame = client.frameadvance(10)
    check("frameadvance", frame > 0, f"frame={frame}")

    ram = client.read_ram()
    check("read_ram", "player_hp" in ram and "room_id" in ram, str(ram))

    block = client.read_block(STAGE_ID, 4)
    check("read_block", len(block) == 4, f"stage/room/cam/old={block}")

    client.send_buttons({"cross": True})
    client.frameadvance(2)
    client.send_buttons({})
    check("buttons", True)

    img = client.screenshot(SHOT_PATH)
    check("screenshot", img.ndim == 3 and img.shape[2] == 3, f"shape={img.shape}")

    Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    client.save_savestate(STATE_PATH)
    client.frameadvance(2)
    client.load_savestate(STATE_PATH)
    check("savestate roundtrip", Path(STATE_PATH).exists())

    client.set_speed(400)
    client.frameadvance(20)
    client.set_speed(100)
    check("speed", True)

    client.quit()
    client.close()

    print(f"done: {failures} failures", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
