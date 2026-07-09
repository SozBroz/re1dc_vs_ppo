"""Create the Jill new-game savestate (episode reset anchor).

Loads the pre-character-select recon checkpoint, mashes to character select,
moves the highlight to Jill (right), confirms, then mashes through the intro
until the game gives control (HP set + character_id == 1). Saves:
  states/jill_start.State
  data/recon/jill_start.png

Verifies character_id == 1 (Jill); exits nonzero otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

CHECKPOINT = "D:/re1_rl/states/recon_f1950.State"
OUT_STATE = "D:/re1_rl/states/jill_start.State"
OUT_SHOT = "D:/re1_rl/data/recon/jill_start.png"
SHOT_DIR = Path("D:/re1_rl/data/recon")


def tap(client: BizHawkClient, button: str, hold: int = 2, release: int = 8) -> None:
    client.send_buttons({button: True})
    client.frameadvance(hold)
    client.send_buttons({})
    client.frameadvance(release)


def main() -> int:
    client = BizHawkClient(timeout=300.0)
    client.start_server()
    print("listening; launch EmuHawk now", flush=True)
    client.wait_for_client()
    print("connected", flush=True)

    client.set_speed(6400)
    client.load_savestate(CHECKPOINT)
    client.frameadvance(10)

    # From f1950 (main menu area, room byte 27): tap Cross until the char
    # select is reachable, then Right to Jill, Cross to confirm. The recon
    # run reached char select ~f2100, i.e. within ~150 frames of mashing.
    for _ in range(6):
        tap(client, "cross", hold=2, release=20)
    client.screenshot(str(SHOT_DIR / "charselect_before.png"))

    tap(client, "right", hold=2, release=20)
    client.screenshot(str(SHOT_DIR / "charselect_after_right.png"))
    tap(client, "cross", hold=2, release=20)

    # Mash through FMV / narration until HP initialises.
    hp = 0
    char = -1
    for step in range(4000):
        if step % 4 == 0:
            client.send_buttons({"cross": True})
        elif step % 4 == 2:
            client.send_buttons({"start": True})
        else:
            client.send_buttons({})
        client.frameadvance(1)
        if step % 50 == 0:
            ram = client.read_ram()
            hp, char = ram["player_hp"], ram["character_id"]
            if hp not in (0, 65535):
                break

    # Let the opening cutscene inputs settle, then a short buffer so the
    # savestate lands in (or right at) player control.
    client.send_buttons({})
    client.frameadvance(30)
    ram = client.read_ram()
    print(f"started: hp={ram['player_hp']} char={ram['character_id']}"
          f" stage={ram['stage_id']} room={ram['room_id']}", flush=True)

    client.save_savestate(OUT_STATE)
    client.screenshot(OUT_SHOT)
    client.set_speed(100)
    client.quit()
    client.close()

    if ram["character_id"] != 1:
        print("FAIL: character is not Jill", flush=True)
        return 1
    print("OK: jill_start.State saved", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
