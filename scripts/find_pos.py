"""Empirically locate the player position bytes.

Loads jill_control.State, then:
  1. baseline: advance 60 frames with NO input, diff a wide RAM block
     (collects bytes that change on their own: timers, animation, RNG).
  2. walk: hold Up 60 frames, diff again.
  3. candidates = changed-under-walk MINUS changed-in-baseline.
Repeats walk with Down (backpedal) to confirm the same bytes move.
Prints candidate offsets grouped into runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

IN_STATE = "D:/re1_rl/states/jill_control.State"
# MediaKite CE table: X/Y/Z at exe+0x8351E8/EC/F0, HP at exe+0x83523C
# (HP - X = 0x54). PS1 HP is 0x800C51AC -> predicted X near 0x800C5158.
BLOCK_START = 0x800C5000
BLOCK_LEN = 0x800


def dump(client: BizHawkClient) -> list[int]:
    out: list[int] = []
    for off in range(0, BLOCK_LEN, 0x400):
        out.extend(client.read_block(BLOCK_START + off, 0x400))
    return out


def changed(a: list[int], b: list[int]) -> set[int]:
    return {i for i, (x, y) in enumerate(zip(a, b)) if x != y}


def run_phase(client: BizHawkClient, button: str | None, frames: int = 60) -> set[int]:
    client.load_savestate(IN_STATE)
    client.frameadvance(5)
    # Jill starts facing the door; turn ~180 first so walking isn't wall-clipped
    client.send_buttons({"left": True})
    client.frameadvance(60)
    client.send_buttons({})
    client.frameadvance(5)
    before = dump(client)
    if button:
        client.send_buttons({button: True})
    client.frameadvance(frames)
    client.send_buttons({})
    after = dump(client)
    return changed(before, after)


def main() -> int:
    client = BizHawkClient(timeout=300.0)
    client.start_server()
    print("listening; launch EmuHawk now", flush=True)
    client.wait_for_client()
    print("connected", flush=True)
    client.set_speed(6400)

    base = run_phase(client, None)
    print(f"baseline churn: {len(base)} bytes", flush=True)
    walk = run_phase(client, "up", frames=120)
    back = run_phase(client, "down", frames=120)
    turn = run_phase(client, "left", frames=40)

    cand_walk = (walk - base)
    cand_back = (back - base)
    cand_turn = (turn - base)
    both = cand_walk & cand_back
    print(f"walk-only: {len(cand_walk)}  back-only: {len(cand_back)}  both: {len(both)}",
          flush=True)

    def show(name: str, s: set[int]) -> None:
        offs = sorted(s)
        runs: list[list[int]] = []
        for o in offs:
            if runs and o == runs[-1][-1] + 1:
                runs[-1].append(o)
            else:
                runs.append([o])
        print(name + ":", flush=True)
        for r in runs:
            lo, hi = BLOCK_START + r[0], BLOCK_START + r[-1]
            print(f"  0x{lo:08X}-0x{hi:08X} ({len(r)}B)", flush=True)

    show("walk+back (position candidates)", both)
    show("turn-only (facing candidates)", cand_turn - cand_walk)

    client.set_speed(100)
    client.quit()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
