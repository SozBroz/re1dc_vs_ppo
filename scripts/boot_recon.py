"""Recon boot: mash through menus via the bridge, dropping screenshots and
savestates every CHECKPOINT frames until HP initialises (game started).

Outputs:
  data/recon/shot_f{N}.png     -- screenshot at frame N
  states/recon_f{N}.State      -- savestate at frame N
  data/recon/recon_log.txt     -- frame/hp/room trace

Afterwards a human (or agent reading the PNGs) picks the checkpoint just
before character select and scripts the Jill inputs from there.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

SHOT_DIR = Path("D:/re1_rl/data/recon")
STATE_DIR = Path("D:/re1_rl/states")
CHECKPOINT = 150
MAX_FRAMES = 4500


def main() -> int:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(SHOT_DIR / "recon_log.txt", "w")

    client = BizHawkClient(timeout=300.0)
    client.start_server()
    print("listening; launch EmuHawk now", flush=True)
    client.wait_for_client()
    print("connected", flush=True)

    client.set_speed(6400)

    frame = 0
    n = 0
    while frame < MAX_FRAMES:
        # mash pattern: Cross on step%4==0, Start on step%4==2
        if n % 4 == 0:
            client.send_buttons({"cross": True})
        elif n % 4 == 2:
            client.send_buttons({"start": True})
        else:
            client.send_buttons({})
        frame = client.frameadvance(1)
        n += 1

        if frame % CHECKPOINT < 1:
            ram = client.read_ram()
            client.screenshot(str(SHOT_DIR / f"shot_f{frame}.png"))
            client.save_savestate(str(STATE_DIR / f"recon_f{frame}.State"))
            line = (
                f"frame={frame} hp={ram['player_hp']} stage={ram['stage_id']}"
                f" room={ram['room_id']} char={ram['character_id']}"
            )
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()
            if ram["player_hp"] not in (0, 65535):
                print("HP initialised -- game started; stopping", flush=True)
                break

    client.set_speed(100)
    client.quit()
    client.close()
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
