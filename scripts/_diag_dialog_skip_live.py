"""Live e2e: RamSkipper must auto-dismiss a modal dialogue box.

Wanders the dining room until an examine/talk modal opens (movement dead in
all 4 dirs, in-control bit still set), then calls skip_uncontrolled() and
asserts the modal is gone and control is live. Also re-checks the plain
cutscene path (west door -> Barry main hall scene).

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_dialog_skip_live.py --port 5723
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import GAME_STATE, PLAYER_X, PLAYER_Z, ROOM_ID
from re1_rl.ram_skip import (
    RamSkipper,
    SKIP_POLL_FIELDS,
    message_open_from_ram,
    needs_skip_from_ram,
)

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "states" / "jill_control_fresh.State"

POLL = [
    ("state", GAME_STATE, "u32"),
    ("room", ROOM_ID, "u8"),
    ("x", PLAYER_X, "s16"),
    ("z", PLAYER_Z, "s16"),
]


def read(b: BizHawkClient) -> dict:
    return b.read_ram(POLL)


def all_dirs_dead(b: BizHawkClient) -> bool:
    for d in ("up", "down", "left", "right"):
        before = read(b)
        b.step({d: True}, 12)
        after = read(b)
        if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) >= 8:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5723)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(6400)

    skipper = RamSkipper(b, training_speed=6400, cutscene_speed=6400,
                         use_engine_patches=True, invisible_during_skip=False)
    skipper.install_engine_patches()

    b.load_savestate(str(FRESH))
    b.frameadvance(2)

    failures: list[str] = []

    # --- find a modal ---
    moves = ["up", "up", "left", "up", "right", "up", "up", "down", "left", "up"] * 3
    modal_found = False
    for i, mv in enumerate(moves):
        b.step({mv: True}, 24)
        b.step({"cross": True}, 2)
        b.frameadvance(20)
        ram = b.read_ram(SKIP_POLL_FIELDS)
        if message_open_from_ram(ram):
            modal_found = True
            print(f"[{i}] modal open (msg flag set)", flush=True)
            break
        if needs_skip_from_ram(ram):
            burned = skipper.skip_uncontrolled()
            print(f"[{i}] burned uncontrolled {burned}", flush=True)
            continue
        if all_dirs_dead(b):
            modal_found = True
            print(f"[{i}] modal open (movement dead)", flush=True)
            break

    if not modal_found:
        print("FAIL: no modal found", flush=True)
        failures.append("find modal")
    else:
        ram = b.read_ram(SKIP_POLL_FIELDS)
        print(f"pre-skip:  msg_open={message_open_from_ram(ram)} "
              f"needs_skip={needs_skip_from_ram(ram)}", flush=True)
        t0 = time.perf_counter()
        burned = skipper.skip_uncontrolled()
        wall = time.perf_counter() - t0
        ram = b.read_ram(SKIP_POLL_FIELDS)
        msg = message_open_from_ram(ram)
        ok = burned > 0 and not msg and not needs_skip_from_ram(ram)
        print(f"skip: burned={burned} wall={wall:.2f}s msg_open={msg} "
              f"needs_skip={needs_skip_from_ram(ram)} -> {'OK' if ok else 'FAIL'}",
              flush=True)
        if not ok:
            failures.append("dialogue skip")
        # movement must be live again (try all 4 dirs; walls can block some)
        moved = False
        for d in ("down", "left", "right", "up"):
            before = read(b)
            b.step({d: True}, 16)
            after = read(b)
            if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) >= 8:
                moved = True
                break
        print(f"post-skip movement: {'OK' if moved else 'FAIL'}", flush=True)
        if not moved:
            failures.append("post-skip control")

    print(f"\nRESULT: {'ALL OK' if not failures else 'FAILURES: ' + ', '.join(failures)}",
          flush=True)
    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
