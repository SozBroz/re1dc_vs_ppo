"""Bit-flip hunt for SCD / work flags after scripted room events.

Enter-driven before/after snapshots; reports single-bit changes in the
door-flag neighborhood (default 0x800C8600-0x800C8800). Confirmed flags
can be named interactively and merged into data/scd_work_flags.json.

Run:
  D:\\re1_rl\\venv\\Scripts\\python.exe scripts\\hunt_scd_flags.py --port 5555
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from re1_rl.bizhawk_bridge import BizHawkClient
from re1_rl.memory_map import DOOR_FLAGS, PS1_MAINRAM_BASE, ROOM_ID

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "states" / "jill_control_fresh.State"
FLAGS_JSON = ROOT / "data" / "scd_work_flags.json"

MAINRAM_SIZE = 0x200000
CHUNK = 0x10000
DEFAULT_LO = 0x800C8600
DEFAULT_HI = 0x800C8800


def _pause(msg: str) -> None:
    print(msg, flush=True)
    input()


def read_range(client: BizHawkClient, lo: int, hi: int) -> list[int]:
    size = hi - lo
    out: list[int] = []
    for off in range(0, size, CHUNK):
        out.extend(client.read_block(lo + off, min(CHUNK, size - off)))
    return out


def bit_flips(
    before: list[int],
    after: list[int],
    lo: int,
) -> list[dict[str, int | str]]:
    n = min(len(before), len(after))
    flips: list[dict] = []
    for i in range(n):
        xor = before[i] ^ after[i]
        if xor == 0:
            continue
        addr = lo + i
        for bit in range(8):
            if xor & (1 << bit):
                old_bit = (before[i] >> bit) & 1
                new_bit = (after[i] >> bit) & 1
                flips.append(
                    {
                        "address": f"0x{addr:08X}",
                        "bit": bit,
                        "old": old_bit,
                        "new": new_bit,
                        "transition": f"{old_bit}->{new_bit}",
                    }
                )
    return flips


def load_flags_db() -> dict:
    if FLAGS_JSON.is_file():
        return json.loads(FLAGS_JSON.read_text(encoding="utf-8"))
    return {
        "flags": [],
        "_meta": {
            "description": "SCD work flags confirmed via hunt_scd_flags.py",
            "anchor": f"0x{DOOR_FLAGS:08X}",
        },
    }


def save_flags_db(db: dict) -> None:
    FLAGS_JSON.parent.mkdir(parents=True, exist_ok=True)
    FLAGS_JSON.write_text(json.dumps(db, indent=2), encoding="utf-8")
    print(f"Updated {FLAGS_JSON}", flush=True)


def flag_key(entry: dict) -> tuple[str, int]:
    return (str(entry["address"]), int(entry["bit"]))


def merge_flag(db: dict, entry: dict) -> None:
    existing = {flag_key(f): f for f in db.get("flags", [])}
    k = flag_key(entry)
    if k in existing:
        existing[k].update(entry)
        print(f"  merged existing {entry['name']}", flush=True)
    else:
        db.setdefault("flags", []).append(entry)
        print(f"  appended {entry['name']}", flush=True)
    db["_meta"] = {
        **db.get("_meta", {}),
        "updated": datetime.now(timezone.utc).isoformat(),
        "anchor": f"0x{DOOR_FLAGS:08X}",
    }


def prompt_append(flips: list[dict], room_id: str) -> None:
    if not flips:
        return
    print("\nAppend a confirmed flag to scd_work_flags.json?", flush=True)
    for i, f in enumerate(flips):
        print(
            f"  [{i}] {f['address']} bit{f['bit']} {f['transition']}",
            flush=True,
        )
    sel = input("Index to save [empty=skip]: ").strip()
    if not sel:
        return
    try:
        idx = int(sel)
        chosen = flips[idx]
    except (ValueError, IndexError):
        print("Invalid selection.", flush=True)
        return

    name = input("Flag name (e.g. barry_gave_lockpick): ").strip()
    if not name:
        print("Skipped (no name).", flush=True)
        return
    rid = input(f"room_id [{room_id}]: ").strip() or room_id
    unlocks = input("unlocks description: ").strip()
    verified = datetime.now().strftime("%Y-%m-%d")

    entry = {
        "name": name,
        "address": chosen["address"],
        "bit": int(chosen["bit"]),
        "room_id": rid,
        "unlocks": unlocks,
        "verified": verified,
        "transition": chosen["transition"],
    }
    db = load_flags_db()
    merge_flag(db, entry)
    save_flags_db(db)


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt SCD work-flag bit flips.")
    ap.add_argument("--savestate", type=str, default=str(DEFAULT_STATE))
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=DEFAULT_LO)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=DEFAULT_HI)
    ap.add_argument(
        "--full",
        action="store_true",
        help=f"Scan full MainRAM instead of default 0x{DEFAULT_LO:08X}-0x{DEFAULT_HI:08X}",
    )
    args = ap.parse_args()

    lo, hi = args.lo, args.hi
    if args.full:
        lo, hi = PS1_MAINRAM_BASE, PS1_MAINRAM_BASE + MAINRAM_SIZE

    client = BizHawkClient(port=args.port, timeout=600.0)
    client.start_server()
    print(f"listening on port {args.port}; launch EmuHawk", flush=True)
    client.wait_for_client()
    client.set_speed(100)

    client.load_savestate(args.savestate)
    client.frameadvance(2)
    print(f"DOOR_FLAGS anchor: 0x{DOOR_FLAGS:08X}", flush=True)
    print(f"Scan 0x{lo:08X}-0x{hi:08X}. Trigger ONE event per pair.", flush=True)
    print("Examples: emblem placed, Barry lockpick, crow puzzle solved.", flush=True)

    event_num = 0
    while True:
        event_num += 1
        _pause(f"[event {event_num}] BEFORE event — set up savestate pose, then Enter")
        room = int(client.read_ram([("room", ROOM_ID, "u8")])["room"])
        before = read_range(client, lo, hi)
        _pause(f"[event {event_num}] Trigger event in EmuHawk, then Enter for AFTER")
        after = read_range(client, lo, hi)
        flips = bit_flips(before, after, lo)

        print(f"\n=== Event {event_num} room={room}: {len(flips)} bit flip(s) ===", flush=True)
        for f in flips:
            print(
                f"  {f['address']} bit{f['bit']} {f['transition']}",
                flush=True,
            )

        prompt_append(flips, str(room))

        again = input("\nAnother event? [Y/n]: ").strip().lower()
        if again in ("n", "no"):
            break

    client.quit()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
