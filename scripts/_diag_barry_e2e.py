"""E2E wall-clock: full Barry main-hall scene through the harness skip path.

Enters the west dining door, then mimics play_human's auto-skip loop (poll
needs_skip_from_ram, call skip_uncontrolled in 1200-frame chunks) and times
the whole scene until true control. Success: total wall < 8s and movement
probe passes.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\_diag_barry_e2e.py --port 5729
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import PLAYER_X, PLAYER_Z, ROOM_ID
from re1_rl.ram_skip import RamSkipper, SKIP_POLL_FIELDS, needs_skip_from_ram

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "states" / "jill_control_fresh.State"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5729)
    args = ap.parse_args()

    b = BizHawkClient(port=args.port, timeout=300.0)
    b.start_server()
    print(f"listening; launch EmuHawk on port {args.port}", flush=True)
    b.wait_for_client()
    b.set_speed(100)  # human play speed while "in control"

    skipper = RamSkipper(b, training_speed=100, cutscene_speed=6400,
                         use_engine_patches=True, invisible_during_skip=True)
    skipper.install_engine_patches()

    b.load_savestate(str(FRESH))
    b.frameadvance(2)

    # walk to the west door at human speed (same as a player would)
    b.set_speed(6400)  # don't waste bench time on the walk itself
    b.step({"left": True}, 300)
    b.step({"up": True}, 30)
    b.set_speed(100)
    b.step({"cross": True}, 4)

    # harness-style loop: poll, skip, repeat
    t0 = time.perf_counter()
    total = 0
    polls = 0
    while True:
        ram = b.read_ram(SKIP_POLL_FIELDS)
        polls += 1
        if not needs_skip_from_ram(ram):
            break
        total += skipper.skip_uncontrolled(max_frames=1200)
        if time.perf_counter() - t0 > 30:
            print("TIMEOUT: scene not cleared in 30s", flush=True)
            break
    wall = time.perf_counter() - t0

    # ground truth movement; if frozen, keep polling to map the tail
    poll = [("room", ROOM_ID, "u8"), ("x", PLAYER_X, "s16"), ("z", PLAYER_Z, "s16")]

    def probe() -> bool:
        for d in ("down", "up", "left", "right"):
            before = b.read_ram(poll)
            b.step({d: True}, 12)
            after = b.read_ram(poll)
            if abs(after["x"] - before["x"]) + abs(after["z"] - before["z"]) >= 8:
                return True
        return False

    moved = probe()
    tail_t0 = time.perf_counter()
    while not moved and time.perf_counter() - tail_t0 < 15:
        ram = b.read_ram(SKIP_POLL_FIELDS)
        print(f"  tail: needs_skip={needs_skip_from_ram(ram)} "
              f"mode=0x{int(ram['game_mode']):02X} msg=0x{int(ram['msg_flag']):02X} "
              f"scene=0x{int(ram['scene_flag']):02X} room={ram['room_id']}",
              flush=True)
        if needs_skip_from_ram(ram):
            extra = skipper.skip_uncontrolled(max_frames=1200)
            total += extra
            print(f"  tail: burned {extra} more", flush=True)
        else:
            b.frameadvance(30)
        moved = probe()
    wall = time.perf_counter() - t0
    room = b.read_ram(poll)["room"]

    ok = moved and wall < 8.0 and total > 0
    print(f"scene: burned={total} wall={wall:.2f}s polls={polls} "
          f"room={room} moved={moved} -> {'OK' if ok else 'FAIL'}", flush=True)

    b.set_speed(100)
    b.quit()
    b.close()
    print("DIAG_DONE", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
