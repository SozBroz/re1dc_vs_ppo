"""Diff hunt for the 'Press X' interaction-prompt byte(s).

Operator alternates AWAY vs AT interactable snapshots (Enter-driven).
Reports bytes consistently different across all AT vs all AWAY rounds.
Excludes known-noisy player-position and timer regions.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\hunt_interaction_prompt.py --port 5555
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\hunt_interaction_prompt.py --fast --rounds 3
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import MESSAGE_FLAG, PS1_MAINRAM_BASE

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "states" / "jill_control_fresh.State"

MAINRAM_SIZE = 0x200000
CHUNK = 0x10000
FAST_LO = 0x800C0000
FAST_HI = 0x800D0000

# Noisy regions: player entity block, game timers near CAM/flags cluster.
EXCLUDE_RANGES = [
    (0x800C5100, 0x800C5200),
    (0x800C8670, 0x800C8680),
]


def _pause(msg: str) -> None:
    print(msg, flush=True)
    input()


def in_exclude(addr: int) -> bool:
    return any(lo <= addr < hi for lo, hi in EXCLUDE_RANGES)


def read_range(client: BizHawkClient, lo: int, hi: int) -> list[int]:
    size = hi - lo
    out: list[int] = []
    for off in range(0, size, CHUNK):
        out.extend(client.read_block(lo + off, min(CHUNK, size - off)))
    return out


def snapshot_block(client: BizHawkClient, lo: int, hi: int, tag: str) -> list[int]:
    msg = int(client.read_ram([("msg", MESSAGE_FLAG, "u8")])["msg"])
    print(f"  [{tag}] MESSAGE_FLAG 0x{MESSAGE_FLAG:08X} = 0x{msg:02X} (bit7={'1' if msg & 0x80 else '0'})",
          flush=True)
    return read_range(client, lo, hi)


def consistent_diffs(
    away_snaps: list[list[int]],
    at_snaps: list[list[int]],
    lo: int,
) -> list[tuple[int, set[int], set[int]]]:
    """Bytes where every AT value set disjoint from every AWAY value set."""
    n = min(len(s) for s in away_snaps + at_snaps)
    away_vals: dict[int, set[int]] = defaultdict(set)
    at_vals: dict[int, set[int]] = defaultdict(set)
    for snap in away_snaps:
        for i in range(n):
            away_vals[i].add(snap[i])
    for snap in at_snaps:
        for i in range(n):
            at_vals[i].add(snap[i])

    hits: list[tuple[int, set[int], set[int]]] = []
    for i in range(n):
        addr = lo + i
        if in_exclude(addr):
            continue
        av, tv = away_vals[i], at_vals[i]
        if not av or not tv:
            continue
        if av.isdisjoint(tv):
            hits.append((addr, av, tv))
    return sorted(hits, key=lambda t: t[0])


def cluster_addr_hits(
    hits: list[tuple[int, set[int], set[int]]],
) -> list[list[tuple[int, set[int], set[int]]]]:
    if not hits:
        return []
    runs: list[list[tuple[int, set[int], set[int]]]] = []
    for h in hits:
        if runs and h[0] == runs[-1][-1][0] + 1:
            runs[-1].append(h)
        else:
            runs.append([h])
    return runs


def fmt_vals(s: set[int]) -> str:
    return "{" + ",".join(f"0x{v:02X}" for v in sorted(s)) + "}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt interaction-prompt RAM bytes.")
    ap.add_argument("--savestate", type=str, default=str(DEFAULT_STATE))
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--rounds", type=int, default=3, help="AWAY/AT pairs per side")
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=PS1_MAINRAM_BASE)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=PS1_MAINRAM_BASE + MAINRAM_SIZE)
    ap.add_argument(
        "--fast",
        action="store_true",
        help=f"Scan 0x{FAST_LO:08X}-0x{FAST_HI:08X} instead of full MainRAM",
    )
    args = ap.parse_args()

    lo, hi = args.lo, args.hi
    if args.fast:
        lo, hi = FAST_LO, FAST_HI

    client = BizHawkClient(port=args.port, timeout=600.0)
    client.start_server()
    print(f"listening on port {args.port}; launch EmuHawk", flush=True)
    client.wait_for_client()
    client.set_speed(100)

    client.load_savestate(args.savestate)
    client.frameadvance(2)
    print(f"Scan range 0x{lo:08X}-0x{hi:08X} ({hi - lo} bytes)", flush=True)
    print("MESSAGE_FLAG tracks modal text windows; prompt may use a different byte.", flush=True)

    away_snaps: list[list[int]] = []
    at_snaps: list[list[int]] = []

    for r in range(1, args.rounds + 1):
        _pause(f"[round {r}/{args.rounds}] Stand AWAY from interactables, then Enter")
        away_snaps.append(snapshot_block(client, lo, hi, f"away#{r}"))
        _pause(f"[round {r}/{args.rounds}] Stand AT door/item (Press X prompt), then Enter")
        at_snaps.append(snapshot_block(client, lo, hi, f"at#{r}"))

    hits = consistent_diffs(away_snaps, at_snaps, lo)
    runs = cluster_addr_hits(hits)

    print(f"\n=== Consistent AT vs AWAY ({len(hits)} bytes, {len(runs)} runs) ===", flush=True)
    for run in runs[:40]:
        a0 = run[0][0]
        a1 = run[-1][0]
        print(f"  0x{a0:08X}-0x{a1:08X} ({len(run)}B)", flush=True)
        for addr, av, tv in run[:8]:
            print(f"    0x{addr:08X}  away={fmt_vals(av)}  at={fmt_vals(tv)}", flush=True)
        if len(run) > 8:
            print(f"    ... +{len(run) - 8} more", flush=True)

    near_msg = [h for h in hits if abs(h[0] - MESSAGE_FLAG) <= 0x20]
    if near_msg:
        print(f"\nNear MESSAGE_FLAG 0x{MESSAGE_FLAG:08X}:", flush=True)
        for addr, av, tv in near_msg:
            print(f"  0x{addr:08X} away={fmt_vals(av)} at={fmt_vals(tv)}", flush=True)

    client.quit()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
