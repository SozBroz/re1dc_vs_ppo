"""Find candidate ROOM_ID / position addresses from a ram_log.csv.

Strategy: the room register is a byte (or u16) that is STABLE while you stand
in a room and CHANGES exactly when you cross a door. Given a CSV of work-RAM
snapshots (from lua/ram_logger.lua) plus the sequence of room hex-ids the human
actually walked through, we score each byte offset by how well its value
transitions line up with room changes.

Two modes:
  1. --rooms 105,106,10A ...  -> if you log the true room order, we find the
     offset whose distinct-value run-lengths match and whose values equal the
     room ids.
  2. no --rooms -> unsupervised: rank offsets by "few long stable runs"
     (room-like) vs noise (position/animation) or constant (uninteresting).

Usage:
  python scripts/find_room_id.py --csv data/ram_log.csv
  python scripts/find_room_id.py --csv data/ram_log.csv --rooms 105,106,107
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def load(csv_path: Path) -> tuple[list[str], list[list[int]]]:
    with csv_path.open() as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [[int(x) for x in row] for row in reader if row]
    return header, rows


def col(rows: list[list[int]], idx: int) -> list[int]:
    return [r[idx] for r in rows]


def run_segments(values: list[int]) -> list[tuple[int, int]]:
    """Return (value, run_length) collapsing consecutive equal values."""
    segs: list[tuple[int, int]] = []
    for v in values:
        if segs and segs[-1][0] == v:
            segs[-1] = (v, segs[-1][1] + 1)
        else:
            segs.append((v, 1))
    return segs


def unsupervised_rank(header: list[str], rows: list[list[int]]) -> list[tuple[str, float, int, int]]:
    """Rank byte columns as room-register candidates.

    Good room register: small number of distinct values, each held for a long
    contiguous run, values in a plausible id range (0x00-0xFF, stage nibble
    1-7). Score favors few transitions with many distinct values overall.
    """
    n = len(rows)
    out = []
    for idx in range(1, len(header)):  # skip 'frame'
        vals = col(rows, idx)
        distinct = set(vals)
        if len(distinct) < 2 or len(distinct) > 40:
            continue  # constant or too noisy
        segs = run_segments(vals)
        transitions = len(segs) - 1
        if transitions == 0:
            continue
        avg_run = n / len(segs)
        # room-like: several distinct values, long stable runs, few transitions
        plausible = all(0 <= v <= 0xFF for v in distinct)
        stage_like = any((v >> 4) in (1, 2, 3, 4, 5, 6, 7) for v in distinct)
        score = avg_run * len(distinct) / (transitions + 1)
        if plausible:
            score *= 1.5
        if stage_like:
            score *= 1.5
        out.append((header[idx], score, len(distinct), transitions))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def supervised_match(
    header: list[str], rows: list[list[int]], rooms: list[int]
) -> list[tuple[str, int]]:
    """Find columns whose ordered distinct-value sequence == the room order."""
    matches = []
    for idx in range(1, len(header)):
        segs = run_segments(col(rows, idx))
        seq = [v for v, _ in segs]
        # compress the human room list the same way (dedupe consecutive)
        want: list[int] = []
        for r in rooms:
            if not want or want[-1] != r:
                want.append(r)
        if seq == want:
            matches.append((header[idx], 100))
        elif len(seq) == len(want) and sum(a == b for a, b in zip(seq, want)) >= len(want) - 1:
            matches.append((header[idx], 90))
    return matches


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/ram_log.csv")
    ap.add_argument("--rooms", default="", help="comma-sep hex room ids walked, e.g. 105,106,107")
    args = ap.parse_args()

    header, rows = load(Path(args.csv))
    print(f"loaded {len(rows)} samples x {len(header) - 1} byte offsets")

    if args.rooms:
        rooms = [int(x, 16) for x in args.rooms.split(",")]
        hits = supervised_match(header, rows, rooms)
        if hits:
            print("EXACT/NEAR room-id offset matches:")
            for name, conf in hits:
                print(f"  {name}  (confidence {conf})")
        else:
            print("no exact match; falling back to unsupervised ranking")
            for name, score, nd, tr in unsupervised_rank(header, rows)[:15]:
                print(f"  {name}  score={score:.1f} distinct={nd} transitions={tr}")
    else:
        print("top ROOM_ID candidates (unsupervised):")
        for name, score, nd, tr in unsupervised_rank(header, rows)[:20]:
            print(f"  {name}  score={score:.1f} distinct={nd} transitions={tr}")


if __name__ == "__main__":
    main()
