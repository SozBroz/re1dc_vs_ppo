"""Advance jill_start.State to the first player-controllable frame.

Strategy: mash through narration/cutscenes; every CHECK frames, probe for
control by holding Up and watching the player position block (0xC8784).
If position moves under input, we are in control -> save jill_control.State.
Falls back to saving after MAX_STEPS with a screenshot for manual review.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import PLAYER_X

IN_STATE = "D:/re1_rl/states/jill_start.State"
OUT_STATE = "D:/re1_rl/states/jill_control.State"
OUT_SHOT = "D:/re1_rl/data/recon/jill_control.png"
MAX_STEPS = 12000


def read_pos(client: BizHawkClient) -> list[int]:
    return client.read_block(PLAYER_X, 12)


def probe_control(client: BizHawkClient) -> bool:
    """Hold Up for 40 frames; True if the position block moved."""
    before = read_pos(client)
    client.send_buttons({"up": True})
    client.frameadvance(40)
    client.send_buttons({})
    after = read_pos(client)
    return before != after


def main() -> int:
    client = BizHawkClient(timeout=300.0)
    client.start_server()
    print("listening; launch EmuHawk now", flush=True)
    client.wait_for_client()
    print("connected", flush=True)

    client.set_speed(6400)
    client.load_savestate(IN_STATE)
    client.frameadvance(5)

    in_control = False
    step = 0
    while step < MAX_STEPS:
        # mash through narration/FMV/cutscene text
        for i in range(200):
            if i % 4 == 0:
                client.send_buttons({"cross": True})
            elif i % 4 == 2:
                client.send_buttons({"start": True})
            else:
                client.send_buttons({})
            client.frameadvance(1)
        step += 200

        if probe_control(client):
            # confirm twice to reject cutscene camera drift
            if probe_control(client):
                in_control = True
                break
        step += 80

    ram = client.read_ram()
    print(f"step={step} in_control={in_control} hp={ram['player_hp']}"
          f" char={ram['character_id']} stage={ram['stage_id']} room={ram['room_id']}",
          flush=True)

    client.save_savestate(OUT_STATE)
    client.screenshot(OUT_SHOT)
    client.set_speed(100)
    client.quit()
    client.close()

    if not in_control or ram["character_id"] != 1:
        print("FAIL: control probe never succeeded (or wrong character)", flush=True)
        return 1
    print("OK: jill_control.State saved", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
