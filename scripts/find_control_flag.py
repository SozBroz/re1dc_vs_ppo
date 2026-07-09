"""Hunt the in-control / cutscene flag.

Phases:
  A) jill_control.State  -- player has control (100 frames idle)
  B) jill_start.State    -- opening narration cutscene (100 frames idle)
  C) from control: roam + mash cross until the room byte changes, sampling
     continuously -- captures a door transition window.

Candidate regions:
  0x800C2FC0-0x800C30C0  (linear-map prediction of GOG gameState 0x7E41C0)
  0x800C8650-0x800C86D0  (stage/room block neighborhood)

A good flag byte is: constant in A, constant-but-different in B, and flips
during C's transition window.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient

CONTROL = "D:/re1_rl/states/jill_control.State"
NARRATION = "D:/re1_rl/states/jill_start.State"

REGIONS = [
    (0x800C2FC0, 0x100),
    (0x800C8650, 0x80),
]


def dump(client: BizHawkClient) -> list[int]:
    out: list[int] = []
    for start, count in REGIONS:
        out.extend(client.read_block(start, count))
    return out


def addr_of(index: int) -> int:
    for start, count in REGIONS:
        if index < count:
            return start + index
        index -= count
    raise IndexError


def sample_phase(client: BizHawkClient, state: str, frames: int = 100) -> list[list[int]]:
    client.load_savestate(state)
    client.frameadvance(5)
    samples = []
    for _ in range(frames // 10):
        client.frameadvance(10)
        samples.append(dump(client))
    return samples


def constant_values(samples: list[list[int]]) -> dict[int, int]:
    """index -> value for bytes constant across all samples."""
    n = len(samples[0])
    out = {}
    for i in range(n):
        vals = {s[i] for s in samples}
        if len(vals) == 1:
            out[i] = samples[0][i]
    return out


def main() -> int:
    client = BizHawkClient(timeout=300.0)
    client.start_server()
    print("listening; launch EmuHawk now", flush=True)
    client.wait_for_client()
    print("connected", flush=True)
    client.set_speed(6400)

    a = constant_values(sample_phase(client, CONTROL))
    b = constant_values(sample_phase(client, NARRATION))
    static_diff = {i: (a[i], b[i]) for i in a.keys() & b.keys() if a[i] != b[i]}
    print(f"bytes constant in both phases but different: {len(static_diff)}", flush=True)
    for i, (va, vb) in sorted(static_diff.items()):
        print(f"  0x{addr_of(i):08X}: control={va:3d} narration={vb:3d}", flush=True)

    # Phase C: trigger a door transition from control, sampling the candidates
    cands = sorted(static_diff.keys())
    print("--- door transition trace (roam + cross) ---", flush=True)
    client.load_savestate(CONTROL)
    client.frameadvance(5)
    room0 = client.read_ram([("room", 0x800C8661, "u8")])["room"]
    transition_seen = False
    for step in range(600):
        # backpedal-ish roam: alternate walking fwd and pressing cross
        if step % 8 < 5:
            client.step({"up": True}, 10)
        else:
            client.step({"cross": True}, 10)
        vals = dump(client)
        room = client.read_ram([("room", 0x800C8661, "u8")])["room"]
        marked = " ".join(
            f"0x{addr_of(i):X}={vals[i]}" for i in cands[:12]
        )
        if step % 10 == 0 or room != room0:
            print(f"  step={step} room={room} {marked}", flush=True)
        if room != room0:
            transition_seen = True
            print("  ROOM CHANGED -- transition captured above", flush=True)
            # keep sampling a bit to see values settle in the new room
            for _ in range(10):
                client.frameadvance(10)
                vals = dump(client)
                print("    post:", " ".join(f"0x{addr_of(i):X}={vals[i]}" for i in cands[:12]),
                      flush=True)
            break

    if not transition_seen:
        print("no room change in 600 steps; rerun with different roam pattern",
              flush=True)

    client.set_speed(100)
    client.quit()
    client.close()
    print("FLAG_HUNT_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
