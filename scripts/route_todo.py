"""Print the key-item TODO for the Jill any% route, human-readable.

Usage:
    python scripts/route_todo.py            # full checklist + summary
    python scripts/route_todo.py --rooms    # also per-room pickup counts
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from re1_rl.item_todo import ItemTracker, RoomItems, build_item_todo

ROUTE = PROJECT_ROOT / "data" / "route_jill_anypct.json"
ROOM_ITEMS = PROJECT_ROOT / "data" / "room_items.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rooms", action="store_true",
                    help="also print per-room pickup inventory from room_items.json")
    args = ap.parse_args()

    todo = build_item_todo(ROUTE)
    tracker = ItemTracker(todo)
    print(tracker.format_checklist())

    by_room = Counter(t.room_id for t in todo)
    print(f"\nroute items by room ({len(by_room)} rooms):")
    for room, n in by_room.most_common():
        print(f"  {room}: {n}")

    needed = Counter()
    for t in todo:
        for r in t.required_items:
            needed[r] += 1
    if needed:
        print("\nitems required as prerequisites (gate items):")
        for item, n in needed.most_common():
            print(f"  {item}: needed by {n} step(s)")

    if args.rooms:
        ri = RoomItems(ROOM_ITEMS)
        if not ri.loaded:
            print(f"\n[!] {ROOM_ITEMS} not present yet -- run the room-items "
                  "research pass to enable per-room remaining counts")
        else:
            print(f"\nper-room pickups ({len(ri.rooms)} rooms in table):")
            for room in sorted(ri.rooms):
                items = ri.items_in_room(room)
                if not items:
                    continue
                names = ", ".join(
                    f"{it['name']}{' [KEY]' if it.get('key_item') else ''}"
                    for it in items
                )
                print(f"  {room}: {len(items)} pickups -- {names}")


if __name__ == "__main__":
    main()
